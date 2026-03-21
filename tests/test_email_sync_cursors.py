from __future__ import annotations

from datetime import UTC, datetime

from agent_sandbox.email.cursor_store import DurableCursorStore, EmailSyncCursor
from agent_sandbox.email.gmail_provider import GmailEmailProvider
from agent_sandbox.email.graph_provider import GraphEmailProvider
from agent_sandbox.email.providers import EmailMessageSummary


def _summary(provider: str, mailbox_id: str, folder: str, message_id: str) -> EmailMessageSummary:
    return EmailMessageSummary(
        mailbox_id=mailbox_id,
        provider=provider,
        folder=folder,
        message_id=message_id,
        thread_id=None,
        sender="sender@example.com",
        recipients=["to@example.com"],
        received_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
        unread=True,
        body_snippet="snippet",
        metadata={},
    )


class _StubResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def test_cursor_store_update_is_durable(tmp_path) -> None:
    path = tmp_path / "cursors.json"
    store = DurableCursorStore(path)
    store.update(
        "mailbox-1",
        "inbox",
        lambda current: EmailSyncCursor(
            mailbox_id="mailbox-1",
            folder="inbox",
            provider="gmail",
            primitive_cursor="100",
            cursor_version=(current.cursor_version + 1) if current else 1,
        ),
    )

    reloaded = DurableCursorStore(path).get("mailbox-1", "inbox")
    assert reloaded is not None
    assert reloaded.primitive_cursor == "100"
    assert reloaded.cursor_version == 1


def test_gmail_list_unread_monotonic_cursor_and_dedup(tmp_path, monkeypatch) -> None:
    store = DurableCursorStore(tmp_path / "gmail-cursors.json")
    provider = GmailEmailProvider(
        account_identifier="me",
        folder_mapping={"inbox": "INBOX"},
        auth_secret_refs={"access_token": "GMAIL_ACCESS_TOKEN"},
        timeout_seconds=5.0,
        cursor_store=store,
    )

    steps = iter(
        [
            ([{"id": "m1"}, {"id": "m2"}], "10"),
            ([{"id": "m1"}, {"id": "m3"}], "9"),
        ]
    )
    monkeypatch.setattr(provider, "_list_incremental", lambda **_: next(steps))
    monkeypatch.setattr(
        provider,
        "_to_summary",
        lambda **kwargs: _summary("gmail", kwargs["mailbox_id"], kwargs["folder"], kwargs["payload"]["id"]),
    )

    first = provider.list_unread("mailbox-1", "inbox", 10)
    second = provider.list_unread("mailbox-1", "inbox", 10)

    assert [item.message_id for item in first] == ["m1", "m2"]
    assert [item.message_id for item in second] == ["m3"]
    cursor = store.get("mailbox-1", "inbox")
    assert cursor is not None
    assert cursor.primitive_cursor == "10"


def test_graph_list_unread_uses_delta_cursor_and_dedup(tmp_path, monkeypatch) -> None:
    store = DurableCursorStore(tmp_path / "graph-cursors.json")
    provider = GraphEmailProvider(
        account_identifier="leader@example.com",
        folder_mapping={"inbox": "Inbox"},
        auth_secret_refs={"access_token": "GRAPH_ACCESS_TOKEN"},
        timeout_seconds=5.0,
        cursor_store=store,
    )

    steps = iter(
        [
            ([{"id": "g1"}, {"id": "g2"}], "https://delta.example/1"),
            ([{"id": "g2"}, {"id": "g3"}], "https://delta.example/2"),
        ]
    )
    monkeypatch.setattr(provider, "_list_incremental", lambda **_: next(steps))
    monkeypatch.setattr(
        provider,
        "_to_summary",
        lambda **kwargs: _summary("graph", kwargs["mailbox_id"], kwargs["folder"], kwargs["payload"]["id"]),
    )

    first = provider.list_unread("mailbox-2", "inbox", 10)
    second = provider.list_unread("mailbox-2", "inbox", 10)

    assert [item.message_id for item in first] == ["g1", "g2"]
    assert [item.message_id for item in second] == ["g3"]
    cursor = store.get("mailbox-2", "inbox")
    assert cursor is not None
    assert cursor.primitive_cursor == "https://delta.example/2"


def test_gmail_duplicate_suppression_persists_across_provider_restarts(tmp_path, monkeypatch) -> None:
    store = DurableCursorStore(tmp_path / "gmail-cursors.json")
    provider = GmailEmailProvider(
        account_identifier="me",
        folder_mapping={"inbox": "INBOX"},
        auth_secret_refs={"access_token": "GMAIL_ACCESS_TOKEN"},
        timeout_seconds=5.0,
        cursor_store=store,
    )
    monkeypatch.setattr(provider, "_list_incremental", lambda **_: ([{"id": "m1"}], "10"))
    monkeypatch.setattr(
        provider,
        "_to_summary",
        lambda **kwargs: _summary("gmail", kwargs["mailbox_id"], kwargs["folder"], kwargs["payload"]["id"]),
    )

    first_run = provider.list_unread("mailbox-1", "inbox", 10)
    assert [item.message_id for item in first_run] == ["m1"]

    restarted_provider = GmailEmailProvider(
        account_identifier="me",
        folder_mapping={"inbox": "INBOX"},
        auth_secret_refs={"access_token": "GMAIL_ACCESS_TOKEN"},
        timeout_seconds=5.0,
        cursor_store=DurableCursorStore(tmp_path / "gmail-cursors.json"),
    )
    monkeypatch.setattr(restarted_provider, "_list_incremental", lambda **_: ([{"id": "m1"}], "9"))
    monkeypatch.setattr(
        restarted_provider,
        "_to_summary",
        lambda **kwargs: _summary("gmail", kwargs["mailbox_id"], kwargs["folder"], kwargs["payload"]["id"]),
    )

    second_run = restarted_provider.list_unread("mailbox-1", "inbox", 10)
    assert second_run == []
    cursor = DurableCursorStore(tmp_path / "gmail-cursors.json").get("mailbox-1", "inbox")
    assert cursor is not None
    assert cursor.primitive_cursor == "10"


def test_graph_duplicate_suppression_persists_across_provider_restarts(tmp_path, monkeypatch) -> None:
    store = DurableCursorStore(tmp_path / "graph-cursors.json")
    provider = GraphEmailProvider(
        account_identifier="leader@example.com",
        folder_mapping={"inbox": "Inbox"},
        auth_secret_refs={"access_token": "GRAPH_ACCESS_TOKEN"},
        timeout_seconds=5.0,
        cursor_store=store,
    )
    monkeypatch.setattr(
        provider,
        "_list_incremental",
        lambda **_: ([{"id": "g1"}], "https://delta.example/1"),
    )
    monkeypatch.setattr(
        provider,
        "_to_summary",
        lambda **kwargs: _summary("graph", kwargs["mailbox_id"], kwargs["folder"], kwargs["payload"]["id"]),
    )

    first_run = provider.list_unread("mailbox-2", "inbox", 10)
    assert [item.message_id for item in first_run] == ["g1"]

    restarted_provider = GraphEmailProvider(
        account_identifier="leader@example.com",
        folder_mapping={"inbox": "Inbox"},
        auth_secret_refs={"access_token": "GRAPH_ACCESS_TOKEN"},
        timeout_seconds=5.0,
        cursor_store=DurableCursorStore(tmp_path / "graph-cursors.json"),
    )
    monkeypatch.setattr(
        restarted_provider,
        "_list_incremental",
        lambda **_: ([{"id": "g1"}], "https://delta.example/2"),
    )
    monkeypatch.setattr(
        restarted_provider,
        "_to_summary",
        lambda **kwargs: _summary("graph", kwargs["mailbox_id"], kwargs["folder"], kwargs["payload"]["id"]),
    )

    second_run = restarted_provider.list_unread("mailbox-2", "inbox", 10)
    assert second_run == []
    cursor = DurableCursorStore(tmp_path / "graph-cursors.json").get("mailbox-2", "inbox")
    assert cursor is not None
    assert cursor.primitive_cursor == "https://delta.example/2"


def test_gmail_history_paginates_before_advancing_cursor(monkeypatch) -> None:
    provider = GmailEmailProvider(
        account_identifier="me",
        folder_mapping={"inbox": "INBOX"},
        auth_secret_refs={"access_token": "GMAIL_ACCESS_TOKEN"},
        timeout_seconds=5.0,
    )

    class StubClient:
        def get(self, _url: str, *, headers: dict[str, str], params: dict[str, object]) -> _StubResponse:
            assert headers["Authorization"] == "Bearer test-token"
            page_token = str(params.get("pageToken", ""))
            if not page_token:
                return _StubResponse(
                    {
                        "historyId": "101",
                        "nextPageToken": "page-2",
                        "history": [{"messagesAdded": [{"message": {"id": "m1"}}]}],
                    }
                )
            assert page_token == "page-2"
            return _StubResponse(
                {
                    "historyId": "103",
                    "history": [{"messagesAdded": [{"message": {"id": "m2"}}]}],
                }
            )

    monkeypatch.setenv("GMAIL_ACCESS_TOKEN", "test-token")
    monkeypatch.setattr(
        provider,
        "_fetch_message",
        lambda *_args, **kwargs: {"id": kwargs["message_id"], "labelIds": ["UNREAD"]},
    )

    messages, cursor = provider._list_from_history(
        client=StubClient(),
        label="INBOX",
        history_id="100",
        limit=10,
    )

    assert {message["id"] for message in messages} == {"m1", "m2"}
    assert cursor == "103"


def test_graph_delta_continues_to_delta_link_after_unread_cap(monkeypatch) -> None:
    provider = GraphEmailProvider(
        account_identifier="leader@example.com",
        folder_mapping={"inbox": "Inbox"},
        auth_secret_refs={"access_token": "GRAPH_ACCESS_TOKEN"},
        timeout_seconds=5.0,
    )

    calls: list[str] = []

    class StubClient:
        def get(self, url: str, *, headers: dict[str, str]) -> _StubResponse:
            calls.append(url)
            assert headers["Authorization"] == "Bearer graph-token"
            if url.endswith("/start"):
                return _StubResponse(
                    {
                        "value": [{"id": "g1", "isRead": False}, {"id": "g2", "isRead": False}],
                        "@odata.nextLink": "https://delta.example/page-2",
                    }
                )
            if url.endswith("page-2"):
                return _StubResponse(
                    {
                        "value": [{"id": "g3", "isRead": False}],
                        "@odata.deltaLink": "https://delta.example/final",
                    }
                )
            raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setenv("GRAPH_ACCESS_TOKEN", "graph-token")
    unread, next_link = provider._list_from_delta_link(
        client=StubClient(),
        delta_link="https://delta.example/start",
        limit=1,
    )

    assert [item["id"] for item in unread] == ["g1", "g2"]
    assert next_link == "https://delta.example/final"
    assert calls == ["https://delta.example/start", "https://delta.example/page-2"]

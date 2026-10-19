"""Behavioral coverage of recorded send authority, rich edits, and uncertain writes."""

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json

import httpx
import pytest

from http_client_support import patch_httpx_client
from evander.repositories.records import SqliteTeamsRecordsRepository
from evander_teams_graph_addon.service import GraphTeamsService
from evander_teams_graph_addon.message_editing import TeamsMessageEditError


class Graph:
    def __init__(self, target, thread=None):
        self.target = target
        self.thread = thread
        self.requests = []
        self.current = None
        self.patch_outcome = None
        self.get_error = None
        self.decorate = lambda payload: payload
        self.message_id = "m/å#1"

    def __call__(self, request):
        self.requests.append(request)
        if request.method == "POST":
            payload = json.loads(request.content)
            self.current = self.decorate(
                {
                    **payload,
                    "id": self.message_id,
                    "from": {"user": {"id": "sender-1"}},
                    "replyToId": self.thread,
                    "webUrl": "https://teams.example/message",
                }
            )
            return httpx.Response(201, json=self.current)
        if request.method == "GET":
            return httpx.Response(self.get_error or 200, json=self.current)
        assert request.method == "PATCH"
        body = json.loads(request.content)
        assert set(body) == {"body"}
        assert set(body["body"]) == {"contentType", "content"}
        if callable(self.patch_outcome):
            return self.patch_outcome(request, body)
        if self.patch_outcome:
            return httpx.Response(self.patch_outcome)
        self.current["body"] = body["body"]
        return httpx.Response(204)

    @property
    def patches(self):
        return [r for r in self.requests if r.method == "PATCH"]


def setup_graph(tmp_path, monkeypatch, target="team-a/19:channel@thread.skype", thread=None):
    graph = Graph(target, thread)
    patch_httpx_client(monkeypatch, httpx.MockTransport(graph))
    store = SqliteTeamsRecordsRepository(tmp_path / "teams.db")
    service = GraphTeamsService(
        store,
        token="synthetic-token",
        api_base_url="https://graph.example/v1.0",
        poll_limit=25,
        timeout_seconds=1,
        retry_count=2,
        retry_base_backoff_seconds=0.1,
        retry_max_backoff_seconds=0.5,
        allowed_channel_targets_provider=lambda: [target],
    )
    return graph, store, service


@pytest.mark.parametrize(
    "target,thread,expected",
    [
        (
            "team-a/19:channel@thread.skype",
            None,
            "/teams/team-a/channels/19%3Achannel%40thread.skype/messages/m%2F%C3%A5%231",
        ),
        (
            "team-a/19:channel@thread.skype",
            "root:1",
            "/teams/team-a/channels/19%3Achannel%40thread.skype/messages/root%3A1/replies/m%2F%C3%A5%231",
        ),
        ("chat:19:chat@thread.v2", None, "/chats/19%3Achat%40thread.v2/messages/m%2F%C3%A5%231"),
    ],
)
def test_edit_routes_formatting_204_and_retry_revision(
    tmp_path, monkeypatch, target, thread, expected
):
    graph, store, service = setup_graph(tmp_path, monkeypatch, target, thread)
    sent = service.post_status("Original", target, "session-a", thread_id=thread)
    assert sent["message_revision"] == 1
    result = service.update_message(
        "session-a",
        target,
        sent["message_id"],
        "## Färdig ✓\n\n**Klart** & klart",
        expected_revision=1,
    )
    assert result["status"] == "updated"
    assert result["message_revision"] == 2
    assert result["thread_id"] == (
        None if target.startswith("chat:") else thread or sent["message_id"]
    )
    assert graph.patches[0].url.raw_path.decode() == "/v1.0" + expected
    assert (
        graph.current["body"]["content"]
        == "<h2>Färdig ✓</h2><p><strong>Klart</strong> &amp; klart</p>"
    )
    assert (
        service.update_message(
            "session-a",
            target,
            sent["message_id"],
            "## Färdig ✓\n\n**Klart** & klart",
            expected_revision=1,
        )["status"]
        == "unchanged"
    )
    with pytest.raises(TeamsMessageEditError, match="Stale"):
        service.update_message(
            "session-a", target, sent["message_id"], "Conflicting", expected_revision=1
        )
    assert len(graph.patches) == 1
    assert store.get_outbound_edit_state(target, sent["message_id"]).revision == 2


def test_rich_body_keeps_multiple_mentions_cards_files_images_and_links(tmp_path, monkeypatch):
    graph, store, service = setup_graph(tmp_path, monkeypatch)
    prefix = '<div><p><at id="0">A</at> <at id="1">B</at></p>'
    suffix = '<p><attachment id="file-1"></attachment></p><p><img src="../hostedContents/image-1/$value" alt="image"></p><p><attachment id="card-1"></attachment></p><p><a href="https://files.example/download/1">Report.pdf</a></p></div>'
    extras = {
        "mentions": [
            {"id": i, "mentionText": name, "mentioned": {"user": {"id": f"person-{i}"}}}
            for i, name in enumerate(("A", "B"))
        ],
        "attachments": [
            {
                "id": "file-1",
                "contentType": "reference",
                "contentUrl": "https://files.example/file",
            },
            {
                "id": "card-1",
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": '{"type":"AdaptiveCard","body":[]}',
            },
        ],
    }

    def decorate(payload):
        payload.update(deepcopy(extras))
        payload["body"]["content"] = prefix + payload["body"]["content"] + suffix
        return payload

    graph.decorate = decorate
    sent = service.post_status("Original", graph.target, "session-a")
    before = deepcopy(graph.current)
    result = service.update_message("session-a", graph.target, sent["message_id"], "**Next**")
    assert result["message_revision"] == 2
    assert graph.current["body"]["content"] == prefix + "<p><strong>Next</strong></p>" + suffix
    assert {k: v for k, v in graph.current.items() if k != "body"} == {
        k: v for k, v in before.items() if k != "body"
    }
    assert store.get_outbound_receipt(graph.target, sent["message_id"]).protected == extras


def test_post_file_receipt_preserves_provider_normalized_images_and_generated_links(
    tmp_path, monkeypatch
):
    graph, store, service = setup_graph(tmp_path, monkeypatch)

    def normalize(payload):
        payload.pop("hostedContents", None)
        payload["body"]["content"] = payload["body"]["content"].replace(
            "hostedContents/2/", "hostedContents/permanent-image/"
        )
        return payload

    graph.decorate = normalize
    sent = service.post_file(
        "Original",
        graph.target,
        "session-a",
        [
            {"filename": "report.pdf", "contentUrl": "https://files.example/report.pdf"},
            {"filename": "image.png", "content_type": "image/png", "content": b"synthetic image"},
            {"filename": "guide.docx", "download_url": "https://files.example/download/guide"},
        ],
    )
    receipt = store.get_outbound_receipt(graph.target, sent["message_id"])
    assert receipt.editable is True
    assert "permanent-image" in receipt.body_suffix and "guide.docx" in receipt.body_suffix
    service.update_message("session-a", graph.target, sent["message_id"], "Next")
    assert graph.current["body"]["content"] == "<p>Next</p>" + receipt.body_suffix
    assert len(graph.current["attachments"]) == 1


def test_graph_pretty_printing_is_accepted_but_inline_layout_drift_is_rejected(
    tmp_path, monkeypatch
):
    graph, _, service = setup_graph(tmp_path, monkeypatch)
    graph.decorate = lambda payload: {
        **payload,
        "body": {
            "contentType": "html",
            "content": "<div>\n"
            + payload["body"]["content"].replace("</p><p>", "</p>\n<p>")
            + "\n</div>",
        },
    }
    sent = service.post_status("First\n\nSecond", graph.target, "session-a")
    assert (
        service.update_message("session-a", graph.target, sent["message_id"], "Next")["status"]
        == "updated"
    )
    graph.current["body"]["content"] = graph.current["body"]["content"].replace("Next", "Next\n ")
    with pytest.raises(TeamsMessageEditError):
        service.update_message("session-a", graph.target, sent["message_id"], "Another")
    assert len(graph.patches) == 1


def test_authentication_errors_do_not_expose_provider_credentials(tmp_path, monkeypatch):
    graph, _, service = setup_graph(tmp_path, monkeypatch)
    sent = service.post_status("Original", graph.target, "session-a")
    service.token_provider = lambda: (_ for _ in ()).throw(RuntimeError("private-credential"))
    with pytest.raises(TeamsMessageEditError, match="authentication") as error:
        service.update_message("session-a", graph.target, sent["message_id"], "Next")
    assert "private-credential" not in str(error.value)
    assert not graph.patches


@pytest.mark.parametrize("change", ["duplicate", "unbalanced", "unknown-author"])
def test_unusual_original_layout_posts_successfully_but_cannot_edit(tmp_path, monkeypatch, change):
    graph, store, service = setup_graph(tmp_path, monkeypatch)

    def decorate(payload):
        if change == "duplicate":
            payload["body"]["content"] *= 2
        elif change == "unbalanced":
            payload["body"]["content"] += "<div>"
        else:
            payload["from"] = {}
        return payload

    graph.decorate = decorate
    sent = service.post_status("Original", graph.target, "session-a")
    receipt = store.get_outbound_receipt(graph.target, sent["message_id"])
    assert receipt.editable is False and receipt.uneditable_reason
    with pytest.raises(TeamsMessageEditError, match="uneditable"):
        service.update_message("session-a", graph.target, sent["message_id"], "Next")
    assert not graph.patches


@pytest.mark.parametrize(
    "drift", ["body", "author", "thread", "mentions", "attachments", "deleted"]
)
def test_external_changes_fail_without_patching(tmp_path, monkeypatch, drift):
    graph, _, service = setup_graph(tmp_path, monkeypatch)
    sent = service.post_status("Original", graph.target, "session-a")
    changes = {
        "body": {"contentType": "html", "content": "<p>Human edit</p>"},
        "from": {"user": {"id": "human"}},
        "replyToId": "different-root",
        "mentions": [{"id": 3}],
        "attachments": [{"id": "new-file"}],
        "deletedDateTime": "2026-01-01T00:00:00Z",
    }
    key = {"author": "from", "thread": "replyToId", "deleted": "deletedDateTime"}.get(drift, drift)
    graph.current[key] = changes[key]
    with pytest.raises(TeamsMessageEditError):
        service.update_message("session-a", graph.target, sent["message_id"], "Next")
    assert not graph.patches


def test_uncertain_patch_is_reconciled_after_restart_without_duplicate_write(tmp_path, monkeypatch):
    graph, store, service = setup_graph(tmp_path, monkeypatch)
    sent = service.post_status("Original", graph.target, "session-a")

    def lost_response(request, body):
        graph.current["body"] = body["body"]
        raise httpx.ReadTimeout("secret-looking provider detail", request=request)

    graph.patch_outcome = lost_response
    with pytest.raises(TeamsMessageEditError, match="pending intent") as error:
        service.update_message(
            "session-a", graph.target, sent["message_id"], "Next", expected_revision=1
        )
    assert "secret-looking" not in str(error.value)
    assert store.get_outbound_edit_state(graph.target, sent["message_id"]).pending is not None
    service.message_store = SqliteTeamsRecordsRepository(tmp_path / "teams.db")
    graph.patch_outcome = None
    retried = service.update_message(
        "session-a", graph.target, sent["message_id"], "Next", expected_revision=1
    )
    assert retried["status"] == "unchanged" and retried["message_revision"] == 2
    assert len(graph.patches) == 1
    assert len([r for r in graph.requests if r.method == "POST"]) == 1


def test_unapplied_pending_intent_retries_original_before_accepting_new_text(tmp_path, monkeypatch):
    graph, store, service = setup_graph(tmp_path, monkeypatch)
    sent = service.post_status("Original", graph.target, "session-a")
    graph.patch_outcome = lambda request, body: (_ for _ in ()).throw(
        httpx.ReadTimeout("lost", request=request)
    )
    with pytest.raises(TeamsMessageEditError):
        service.update_message("session-a", graph.target, sent["message_id"], "Next")
    with pytest.raises(TeamsMessageEditError, match="retry its original"):
        service.update_message("session-a", graph.target, sent["message_id"], "Other")
    graph.patch_outcome = None
    assert (
        service.update_message("session-a", graph.target, sent["message_id"], "Next")[
            "message_revision"
        ]
        == 2
    )
    assert store.get_outbound_edit_state(graph.target, sent["message_id"]).pending is None


@pytest.mark.parametrize("code", [403, 404, 429, 500])
def test_provider_failures_never_create_replacement_posts(tmp_path, monkeypatch, code):
    graph, _, service = setup_graph(tmp_path, monkeypatch)
    sent = service.post_status("Original", graph.target, "session-a")
    graph.patch_outcome = code
    with pytest.raises(TeamsMessageEditError, match=str(code)):
        service.update_message("session-a", graph.target, sent["message_id"], "Next")
    assert len(graph.patches) == (3 if code in {429, 500} else 1)
    assert len([r for r in graph.requests if r.method == "POST"]) == 1
    assert graph.current["body"]["content"] == "<p>Original</p>"


def test_successful_patch_requires_verified_readback_and_can_recover(tmp_path, monkeypatch):
    graph, store, service = setup_graph(tmp_path, monkeypatch)
    sent = service.post_status("Original", graph.target, "session-a")
    graph.patch_outcome = lambda request, body: httpx.Response(204)
    with pytest.raises(TeamsMessageEditError, match="could not be verified"):
        service.update_message("session-a", graph.target, sent["message_id"], "Next")
    state = store.get_outbound_edit_state(graph.target, sent["message_id"])
    assert state.pending is not None and state.revision == 1
    graph.patch_outcome = None
    assert (
        service.update_message("session-a", graph.target, sent["message_id"], "Next")[
            "message_revision"
        ]
        == 2
    )
    assert len(graph.patches) == 2


def test_throttled_patch_uses_bounded_retry_and_verifies_the_applied_body(tmp_path, monkeypatch):
    graph, _, service = setup_graph(tmp_path, monkeypatch)
    sent = service.post_status("Original", graph.target, "session-a")

    def throttle_once(request, body):
        if len(graph.patches) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        graph.current["body"] = body["body"]
        return httpx.Response(204)

    graph.patch_outcome = throttle_once
    result = service.update_message("session-a", graph.target, sent["message_id"], "Next")
    assert result["message_revision"] == 2 and len(graph.patches) == 2


def test_deleted_message_read_fails_without_any_patch_or_replacement(tmp_path, monkeypatch):
    graph, _, service = setup_graph(tmp_path, monkeypatch)
    sent = service.post_status("Original", graph.target, "session-a")
    graph.get_error = 404
    with pytest.raises(TeamsMessageEditError, match="404"):
        service.update_message("session-a", graph.target, sent["message_id"], "Next")
    assert not graph.patches
    assert len([r for r in graph.requests if r.method == "POST"]) == 1


def test_concurrent_conflicting_calls_serialize_and_only_one_revision_wins(tmp_path, monkeypatch):
    graph, store, service = setup_graph(tmp_path, monkeypatch)
    sent = service.post_status("Original", graph.target, "session-a")

    def edit(text):
        try:
            return service.update_message(
                "session-a", graph.target, sent["message_id"], text, expected_revision=1
            )
        except TeamsMessageEditError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(edit, ["First", "Second"]))
    assert sum(r is not None for r in results) == 1
    assert len(graph.patches) == 1
    assert store.get_outbound_edit_state(graph.target, sent["message_id"]).revision == 2


def test_receipt_ownership_and_conversation_cannot_be_inferred_from_inbox(tmp_path, monkeypatch):
    graph, _, service = setup_graph(tmp_path, monkeypatch)
    sent = service.post_status("Original", graph.target, "session-a")
    for session, target, message_id, thread in [
        ("session-b", graph.target, sent["message_id"], None),
        ("session-a", "another/channel", sent["message_id"], None),
        ("session-a", graph.target, "human-id", None),
        ("session-a", graph.target, sent["message_id"], "wrong-thread"),
    ]:
        with pytest.raises(TeamsMessageEditError):
            service.update_message(session, target, message_id, "Next", thread_id=thread)
    assert not graph.patches

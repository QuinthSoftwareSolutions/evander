"""Behavioral media, permission, recovery and scheduled-notification regressions."""

import base64
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from io import BytesIO
import json
from types import SimpleNamespace

import httpx
from PIL import Image
import pytest

from evander.communication_artifacts import CommunicationArtifactStore
from evander.effects.providers.base import EffectRuntimeContext
from evander.events.internal import InternalEvent
from evander.events.models import WorkflowNodeEnvelope
from evander.events.scheduled import ScheduledInternalEventStore
from evander.models import SessionManifest, ToolName
from evander.policy import PolicyError
from evander.teams_outbound import TeamsImageVersion
from evander.triggers.providers.event import event_trigger_payload
from evander.workflow.binding import resolve_workflow_bindings
from evander.workflow.contracts import effective_node_output_schema
from evander_teams_graph_addon.commands import TeamsMessageCommands
from evander_teams_graph_addon.progress_images import ProgressSnapshot, render_progress_png
from evander_teams_graph_addon.progress_messages import (
    ProgressMessageCoordinator,
    verified_progress_counts,
)
from evander_teams_graph_addon.workflow import materialize, register_providers
from test_gateway_teams_tools import make_context, make_request_context, call_tool_with_context
from test_message_editing import setup_graph

TARGET = "team-a/channel-1"


def snapshot(sequence=1, **changes):
    return ProgressSnapshot.model_validate(
        {
            "generation_id": "generation-a",
            "outline_sha256": "a" * 64,
            "snapshot_sequence": sequence,
            "document_title": "Installation — Ångström ✓",
            "phase": "writing",
            "chapters_total": 3,
            "chapters_completed": 1,
            "sections_total": 5,
            "sections_completed": 2,
            "status": "Drafting continues.",
            **changes,
        }
    )


@pytest.fixture
def host(tmp_path, monkeypatch, request):
    from evander_teams_graph_addon import media
    import evander.communication_artifacts as artifacts

    clock = [datetime.now(UTC).replace(microsecond=0)]
    monkeypatch.setattr(media, "utc_now", lambda: clock[0])
    monkeypatch.setattr(artifacts, "utc_now", lambda: clock[0])
    monkeypatch.setenv("EVANDER_COMMUNICATION_ARTIFACT_LINK_SECRET", "synthetic-progress-secret")
    runtime = make_context(
        tmp_path,
        communication_artifact_public_base_url="https://media.example",
        communication_artifact_link_ttl_seconds=getattr(request, "param", 600),
    )
    session = SessionManifest(
        session_id="session-a",
        allowed_tools=[
            ToolName.TEAMS_POST_FILE,
            ToolName.TEAMS_UPDATE_MESSAGE,
            ToolName.TEAMS_POST_STATUS,
        ],
        teams_channel_allowlist=[TARGET, "chat:existing-chat"],
        communication_file_send_enabled=True,
        communication_file_require_approval_for_send=False,
        expires_at=clock[0] + timedelta(days=1),
    )
    runtime.session_repository.upsert_session(session)
    store = CommunicationArtifactStore(tmp_path)

    def stage(s=None, owner="session-a", **kw):
        return store.stage(
            provider="teams",
            session_id=owner,
            content=render_progress_png(s or snapshot()),
            original_filename="progress.png",
            mime_type="image/png",
            **kw,
        ).artifact_id

    return SimpleNamespace(
        runtime=runtime,
        session=session,
        store=store,
        stage=stage,
        clock=clock,
        commands=TeamsMessageCommands(runtime),
    )


def post(host, artifact=None, **kw):
    artifact = artifact or host.stage()
    return host.commands.post_file(
        "session-a",
        TARGET,
        "Starting",
        [artifact],
        editable_image_artifact_id=artifact,
        idempotency_key="start",
        **kw,
    )


def test_renderer_has_independent_counts_and_stable_pixels():
    counts = verified_progress_counts(
        {"a": ["1", "2"], "b": ["3", "4"], "c": ["5"]},
        [
            {
                "section_id": "1",
                "status": "completed",
                "sha256": "b" * 64,
                "generation_id": "g",
                "outline_sha256": "a" * 64,
            },
            {
                "section_id": "2",
                "status": "completed",
                "sha256": "c" * 64,
                "generation_id": "g",
                "outline_sha256": "a" * 64,
            },
            {
                "section_id": "3",
                "status": "queued",
                "sha256": "d" * 64,
                "generation_id": "g",
                "outline_sha256": "a" * 64,
            },
            {
                "section_id": "4",
                "status": "completed",
                "sha256": "e" * 64,
                "generation_id": "old",
                "outline_sha256": "a" * 64,
            },
            {
                "section_id": "5",
                "status": "completed",
                "sha256": "f" * 64,
                "generation_id": "g",
                "outline_sha256": "old",
            },
        ],
        {"1": "b" * 64, "2": "c" * 64, "3": "d" * 64, "4": "e" * 64, "5": "f" * 64},
        generation_id="g",
        outline_sha256="a" * 64,
    )
    assert counts == {
        "chapters_total": 3,
        "sections_total": 5,
        "sections_completed": 2,
        "chapters_completed": 1,
    }
    data = render_progress_png(snapshot(**counts))
    assert data == render_progress_png(snapshot(sequence=99, **counts))
    image = Image.open(BytesIO(data))
    assert image.format == "PNG" and image.width == 960 and image.height <= 1200
    assert "1/3 chapters drafted" in snapshot(**counts).status_text()
    assert image.getpixel((150, 205)) != image.getpixel((800, 205))


@pytest.mark.parametrize(
    "changes",
    [
        {"sections_completed": 6},
        {"chapters_completed": -1},
        {"sections_total": 10**100},
        {"snapshot_sequence": True},
        {"outline_sha256": "bad"},
        {"phase": "approved"},
        {"document_title": "x" * 161},
        {"status": "x" * 281},
        {"image_url": "https://untrusted.example/image"},
    ],
)
def test_snapshot_rejects_invalid_or_unknown_inputs(changes):
    with pytest.raises(ValueError):
        snapshot(**changes)


def test_zero_totals_and_drafting_complete_do_not_imply_delivery():
    assert render_progress_png(
        snapshot(chapters_total=0, chapters_completed=0, sections_total=0, sections_completed=0)
    )
    s = snapshot(chapters_completed=3, sections_completed=5, phase="reviewing")
    assert "Reviewing" in s.status_text() and "delivered" not in s.status_text().lower()
    assert render_progress_png(snapshot(document_title="W" * 160, status="W" * 280))


def test_stub_post_replace_retry_text_edit_clear_and_restart(host):
    first = host.stage()
    posted = post(host, first)
    assert posted["image_editable"] is True
    assert post(host, first)["message_id"] == posted["message_id"]
    second = host.stage(snapshot(2, sections_completed=3))
    changed = host.commands.update_message(
        "session-a",
        TARGET,
        posted["message_id"],
        "Next",
        expected_revision=1,
        image_artifact_id=second,
    )
    assert changed["status"] == "stubbed" and changed["message_revision"] == 2
    assert changed["image_sha256"] != posted["image_sha256"]
    again = host.commands.update_message(
        "session-a",
        TARGET,
        posted["message_id"],
        "Next",
        expected_revision=1,
        image_artifact_id=second,
    )
    assert again["status"] == "unchanged" and again["message_revision"] == 2
    with pytest.raises(ValueError, match="Stale"):
        host.commands.update_message(
            "session-a", TARGET, posted["message_id"], "Conflict", expected_revision=1
        )
    changed = TeamsMessageCommands(host.runtime).update_message(
        "session-a", TARGET, posted["message_id"], "Reviewing"
    )
    assert changed["image_sha256"] == again["image_sha256"]
    session = host.runtime.session_repository.get_session("session-a")
    host.runtime.session_repository.upsert_session(
        session.model_copy(update={"allowed_tools": [ToolName.TEAMS_UPDATE_MESSAGE]})
    )
    cleared = host.commands.update_message(
        "session-a", TARGET, posted["message_id"], "Delivered for review", clear_image=True
    )
    assert cleared["image_sha256"] is None
    envelope = host.runtime.teams_messages.get(f"{TARGET}/{posted['message_id']}")
    assert "<img" not in envelope.content
    assert len(host.runtime.teams_messages.read_all()) == 1


@pytest.mark.parametrize(
    "failure",
    [
        "other_owner",
        "expired",
        "checksum",
        "mime",
        "dimensions",
        "file_grant",
        "approval",
        "file_policy",
    ],
)
def test_image_publish_failures_do_not_modify_a_message(host, failure):
    posted = post(host)
    artifact = host.stage(owner="other" if failure == "other_owner" else "session-a")
    staged = host.store.get(artifact)
    if failure in {"expired", "mime"}:
        q = json.loads(staged.metadata_path.read_text())
        q.update(
            {"expires_at": (host.clock[0] - timedelta(seconds=1)).isoformat()}
            if failure == "expired"
            else {"mime_type": "image/svg+xml"}
        )
        staged.metadata_path.write_text(json.dumps(q))
    elif failure == "checksum":
        staged.blob_path.write_bytes(b"not a PNG")
    elif failure == "dimensions":
        data = BytesIO()
        Image.new("RGB", (2050, 1)).save(data, format="PNG")
        staged.blob_path.write_bytes(data.getvalue())
        q = json.loads(staged.metadata_path.read_text())
        q.update(sha256=sha256(data.getvalue()).hexdigest(), byte_count=len(data.getvalue()))
        staged.metadata_path.write_text(json.dumps(q))
    elif failure in {"file_grant", "approval", "file_policy"}:
        change = (
            {"allowed_tools": [ToolName.TEAMS_UPDATE_MESSAGE]}
            if failure == "file_grant"
            else {"communication_file_require_approval_for_send": True}
            if failure == "approval"
            else {"communication_file_send_enabled": False}
        )
        host.runtime.session_repository.upsert_session(host.session.model_copy(update=change))
    before = host.runtime.teams_messages.get_outbound_edit_state(TARGET, posted["message_id"])
    with pytest.raises((ValueError, PolicyError, FileNotFoundError)):
        host.commands.update_message(
            "session-a", TARGET, posted["message_id"], "Next", image_artifact_id=artifact
        )
    assert (
        host.runtime.teams_messages.get_outbound_edit_state(TARGET, posted["message_id"]) == before
    )


def test_legacy_receipt_never_acquires_image_authority(host):
    sent = host.runtime.teams_service.post_status("Old", TARGET, "session-a")
    for kwargs in ({"clear_image": True}, {"image_artifact_id": host.stage()}):
        with pytest.raises(PolicyError, match="slot"):
            host.commands.update_message("session-a", TARGET, sent["message_id"], "Next", **kwargs)


def test_initial_post_uncertainty_never_creates_a_second_message(host, monkeypatch):
    service = host.runtime.teams_service
    original = service.post_file
    calls = []

    def lost(*args, **kwargs):
        calls.append(1)
        original(*args, **kwargs)
        raise TimeoutError("Response lost")

    monkeypatch.setattr(service, "post_file", lost)
    artifact = host.stage()
    with pytest.raises(TimeoutError):
        post(host, artifact)
    with pytest.raises(ValueError, match="uncertain"):
        post(host, artifact)
    assert len(calls) == 1 and len(host.runtime.teams_messages.read_all()) == 1


def test_chat_video_delivery_reuses_receipt_and_rejects_a_changed_payload(host):
    def stage(name, content, mime_type):
        return host.store.stage(
            provider="workspace",
            session_id="session-a",
            content=content,
            original_filename=name,
            mime_type=mime_type,
        ).artifact_id

    video = stage("explanation.mp4", b"synthetic video", "video/mp4")
    transcript = stage("transcript.txt", b"synthetic transcript", "text/plain")
    args = ("session-a", "chat:existing-chat", "Here is the explanation", [video, transcript])
    first = host.commands.post_file(*args, idempotency_key="teams-video:request-1")
    second = host.commands.post_file(*args, idempotency_key="teams-video:request-1")
    assert first["message_id"] == second["message_id"]
    assert len(host.runtime.teams_messages.read_all()) == 1
    replacement = stage("replacement.mp4", b"different video", "video/mp4")
    with pytest.raises(ValueError, match="conflicts"):
        host.commands.post_file(
            "session-a",
            "chat:existing-chat",
            "Here is the explanation",
            [replacement, transcript],
            idempotency_key="teams-video:request-1",
        )
    assert len(host.runtime.teams_messages.read_all()) == 1


def test_chat_video_delivery_does_not_repeat_an_uncertain_post(host, monkeypatch):
    video = host.store.stage(
        provider="workspace",
        session_id="session-a",
        content=b"synthetic video",
        original_filename="explanation.mp4",
        mime_type="video/mp4",
    ).artifact_id
    service = host.runtime.teams_service
    original = service.post_file
    calls = []

    def lost(*args, **kwargs):
        calls.append(1)
        original(*args, **kwargs)
        raise TimeoutError("Response lost")

    monkeypatch.setattr(service, "post_file", lost)
    args = ("session-a", "chat:existing-chat", "Here is the explanation", [video])
    with pytest.raises(TimeoutError):
        host.commands.post_file(*args, idempotency_key="teams-video:request-2")
    with pytest.raises(ValueError, match="uncertain"):
        host.commands.post_file(*args, idempotency_key="teams-video:request-2")
    assert len(calls) == 1 and len(host.runtime.teams_messages.read_all()) == 1


def test_mcp_image_inputs_use_bound_session_and_hide_signed_urls_from_audit(host):
    from evander.gateway import create_server

    server = create_server(host.runtime)
    bound = make_request_context(session_id="session-a")
    artifact = host.stage()
    sent = call_tool_with_context(
        server,
        "teams_post_file",
        {
            "channel_target": TARGET,
            "message": "Starting",
            "artifact_ids": [artifact],
            "editable_image_artifact_id": artifact,
            "idempotency_key": "ambient",
        },
        bound,
    )
    second = host.stage(snapshot(2, sections_completed=3))
    changed = call_tool_with_context(
        server,
        "teams_update_message",
        {
            "channel_target": TARGET,
            "message_id": sent["message_id"],
            "message": "Next",
            "image_artifact_id": second,
            "expected_revision": 1,
        },
        bound,
    )
    assert changed["message_revision"] == 2 and "source_url" not in json.dumps(changed)
    audit = (host.runtime.config.runtime_dir / "audit.jsonl").read_text()
    assert "?token=" not in audit and "synthetic-progress-secret" not in audit


def image_version(name):
    return TeamsImageVersion(
        artifact_id="comm_" + name * 32,
        sha256=name * 64,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        renew_at=datetime.now(UTC) + timedelta(minutes=30),
        source_url=f"https://media.example/{name}.png?token=synthetic",
        alt_text="One of three chapters drafted",
    )


@pytest.mark.parametrize(
    "target,thread",
    [
        ("team-a/19:channel@thread.skype", None),
        ("team-a/19:channel@thread.skype", "root/a"),
        ("chat:19:chat@thread.v2", None),
    ],
)
def test_graph_changes_only_managed_image_and_preserves_rich_content(
    tmp_path, monkeypatch, target, thread
):
    graph, store, service = setup_graph(tmp_path, monkeypatch, target, thread)

    def decorate(payload):
        payload["body"]["content"] = (
            '<div><p><at id="0">A</at> <at id="1">B</at></p>'
            + payload["body"]["content"]
            + '<p><attachment id="card"></attachment></p></div>'
        )
        payload["mentions"] = [{"id": 0, "mentionText": "A"}, {"id": 1, "mentionText": "B"}]
        payload.setdefault("attachments", []).append(
            {
                "id": "card",
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": "{}",
            }
        )
        return payload

    graph.decorate = decorate
    a, b = image_version("a"), image_version("b")
    sent = service.post_file(
        "Starting",
        target,
        "session-a",
        [
            {"artifact_id": a.artifact_id, "filename": "managed.png", "mime_type": "image/png"},
            {
                "filename": "protected.png",
                "mime_type": "image/png",
                "content": render_progress_png(snapshot()),
            },
            {"filename": "file.pdf", "contentUrl": "https://files.example/protected.pdf"},
            {"filename": "generated.txt", "download_url": "https://files.example/generated.txt"},
        ],
        thread_id=thread,
        image_version=a,
    )
    protected = deepcopy({k: v for k, v in graph.current.items() if k != "body"})
    result = service.update_message(
        "session-a",
        target,
        sent["message_id"],
        "Next",
        thread_id=thread,
        expected_revision=1,
        image_version=b,
    )
    assert result["message_revision"] == 2
    assert b.source_url.replace("&", "&amp;") in graph.current["body"]["content"]
    assert "hostedContents/2/" in graph.current["body"]["content"]
    assert "https://files.example/generated.txt" in graph.current["body"]["content"]
    assert {k: v for k, v in graph.current.items() if k != "body"} == protected
    assert (
        service.update_message(
            "session-a", target, sent["message_id"], "Next", image_version=b, expected_revision=1
        )["status"]
        == "unchanged"
    )
    service.update_message("session-a", target, sent["message_id"], "Reviewing")
    assert b.source_url in graph.current["body"]["content"]
    service.update_message("session-a", target, sent["message_id"], "Done", clear_image=True)
    assert b.source_url not in graph.current["body"]["content"]
    assert "hostedContents/2/" in graph.current["body"]["content"]
    assert len([r for r in graph.requests if r.method == "POST"]) == 1


def test_graph_ignored_image_patch_and_uncertain_applied_patch(tmp_path, monkeypatch):
    graph, store, service = setup_graph(tmp_path, monkeypatch)
    a, b = image_version("a"), image_version("b")
    sent = service.post_file(
        "Start",
        graph.target,
        "session-a",
        [{"artifact_id": a.artifact_id, "filename": "progress.png", "mime_type": "image/png"}],
        image_version=a,
    )
    graph.patch_outcome = lambda req, body: httpx.Response(204)
    with pytest.raises(ValueError, match="verified"):
        service.update_message(
            "session-a", graph.target, sent["message_id"], "Next", image_version=b
        )
    pending = store.get_outbound_edit_state(graph.target, sent["message_id"]).pending
    assert pending.image.source_url == b.source_url

    def applied(req, body):
        graph.current["body"] = body["body"]
        raise httpx.ReadTimeout("Uncertain", request=req)

    graph.patch_outcome = applied
    with pytest.raises(ValueError, match="retained"):
        service.update_message(
            "session-a",
            graph.target,
            sent["message_id"],
            "Next",
            image_version=b.model_copy(
                update={"source_url": "https://media.example/another-issued-url"}
            ),
        )
    assert b.source_url in graph.current["body"]["content"]
    count = len(graph.patches)
    graph.patch_outcome = None
    assert (
        service.update_message(
            "session-a",
            graph.target,
            sent["message_id"],
            "Next",
            image_version=b,
            expected_revision=1,
        )["status"]
        == "unchanged"
    )
    assert len(graph.patches) == count


def test_ambiguous_image_layout_posts_successfully_but_has_no_image_authority(
    tmp_path, monkeypatch
):
    graph, store, service = setup_graph(tmp_path, monkeypatch)
    a = image_version("a")

    def duplicate(payload):
        payload["body"]["content"] += f'<p><img src="{a.source_url}" alt="second"></p>'
        return payload

    graph.decorate = duplicate
    sent = service.post_file(
        "Start",
        graph.target,
        "session-a",
        [{"artifact_id": a.artifact_id, "filename": "progress.png", "mime_type": "image/png"}],
        image_version=a,
    )
    assert sent["status"] == "posted" and sent["image_editable"] is False
    with pytest.raises(ValueError, match="slot"):
        service.update_message(
            "session-a", graph.target, sent["message_id"], "Next", image_version=image_version("b")
        )
    assert not graph.patches


def coordinator(host):
    timers = {}

    def schedule(**item):
        timers[item["idempotency_key"]] = item
        return "schedule-" + str(len(timers))

    return ProgressMessageCoordinator(
        host.runtime, now=lambda: host.clock[0], schedule=schedule
    ), timers


def test_progress_renewal_duplicates_late_events_and_terminal_clearing(host):
    progress, timers = coordinator(host)
    first = progress.sync("session-a", TARGET, snapshot().model_dump(), enabled=True)
    assert first["status"] == "updated" and len(timers) == 1
    initial = host.runtime.teams_messages.get_outbound_edit_state(TARGET, first["message_id"])
    original_expiry = initial.image.expires_at
    host.clock[0] += timedelta(seconds=301)
    renewed = progress.sync(
        "session-a",
        TARGET,
        enabled=True,
        generation_id="generation-a",
        action="renew",
        source_version=first["source_version"],
    )
    assert renewed["message_id"] == first["message_id"] and renewed["message_revision"] == 2
    state = host.runtime.teams_messages.get_outbound_edit_state(TARGET, first["message_id"])
    assert (
        state.image.sha256 == initial.image.sha256
        and state.image.source_url != initial.image.source_url
    )
    assert host.store.get(state.image.artifact_id).blob_path.read_bytes() == base64.b64decode(
        progress.journal.read(progress.journal.key("session-a", TARGET, None, "generation-a"))[
            "png_base64"
        ]
    )
    duplicate = progress.sync(
        "session-a",
        TARGET,
        enabled=True,
        generation_id="generation-a",
        action="renew",
        source_version=first["source_version"],
    )
    assert duplicate["status"] == "unchanged" and duplicate["message_revision"] == 2
    host.clock[0] = original_expiry + timedelta(seconds=1)
    assert state.image.expires_at > host.clock[0]
    changed = progress.sync(
        "session-a", TARGET, snapshot(2, sections_completed=3).model_dump(), enabled=True
    )
    stale = progress.sync("session-a", TARGET, snapshot(1).model_dump(), enabled=True)
    assert stale["message_revision"] == changed["message_revision"]
    terminal = progress.sync(
        "session-a", TARGET, snapshot(3, phase="cancelled").model_dump(), enabled=True
    )
    assert terminal["source_version"] is None
    assert (
        progress.sync(
            "session-a",
            TARGET,
            enabled=True,
            generation_id="generation-a",
            action="renew",
            source_version=changed["source_version"],
        )["status"]
        == "unchanged"
    )
    assert len(host.runtime.teams_messages.read_all()) == 1


def test_coalescing_and_notification_failures_do_not_repeat_writing(host):
    progress, timers = coordinator(host)
    assert progress.sync("session-a", TARGET, snapshot().model_dump())["status"] == "disabled"
    progress.sync("session-a", TARGET, snapshot().model_dump(), enabled=True)
    second = progress.sync(
        "session-a", TARGET, snapshot(2, sections_completed=3).model_dump(), enabled=True
    )
    assert second["status"] == "coalesced" and second["message_revision"] == 1
    host.clock[0] += timedelta(seconds=31)
    flushed = progress.sync(
        "session-a", TARGET, enabled=True, generation_id="generation-a", action="flush"
    )
    assert flushed["message_revision"] == 2
    host.runtime.session_repository.upsert_session(
        host.session.model_copy(update={"communication_file_send_enabled": False})
    )
    failed = progress.sync(
        "session-a", TARGET, snapshot(3, phase="reviewing").model_dump(), enabled=True
    )
    assert failed["status"] == "notification_failed"
    assert len(host.runtime.teams_messages.read_all()) == 1
    assert not host.runtime.agent_prompt_store.read_raw()["messages"]


def test_progress_delivery_requires_current_matching_receipts(host):
    progress, _ = coordinator(host)
    first = progress.sync("session-a", TARGET, snapshot().model_dump(), enabled=True)
    delivered = snapshot(
        2, phase="delivered", chapters_completed=3, sections_completed=5
    ).model_dump()
    assert (
        progress.sync("session-a", TARGET, delivered, enabled=True)["status"]
        == "notification_failed"
    )
    evidence = {
        "document_sha256": "a" * 64,
        "outputs": {"docx": "b" * 64, "pdf": "c" * 64},
        "receipts": {},
    }
    for fmt, digest in evidence["outputs"].items():
        evidence["receipts"][fmt] = {
            "status": "posted",
            "source_sha256": "a" * 64,
            "output_sha256": digest,
            "message_id": fmt,
            "web_url": f"https://teams.microsoft.com/l/message/channel/{fmt}",
        }
    done = progress.sync("session-a", TARGET, delivered, enabled=True, delivery=evidence)
    assert done["status"] == "updated"
    body = host.runtime.teams_messages.get(f"{TARGET}/{first['message_id']}").content
    assert "owner review" in body and "<img" not in body and "approved" not in body


def test_bindable_providers_are_offline_scoped_and_typed(host):
    registered = register_providers()
    for kind in (
        "effect:teams:render-progress-image",
        "effect:teams:post-file",
        "effect:teams:sync-progress-message",
    ):
        assert kind in registered.effect_providers
    node = WorkflowNodeEnvelope.model_validate(
        {
            "id": "render",
            "display_name": "Render",
            "type": "effect:teams:render-progress-image",
            "config": {
                "session_id": "${{prepare.session_id}}",
                "snapshot": snapshot().model_dump(),
            },
        }
    )
    definition = materialize(node)
    assert "artifact_id" in effective_node_output_schema(definition)
    cls = registered.effect_providers[node.type]
    effect = cls()
    effect.initialize(
        effect_definition=definition,
        runtime_context=EffectRuntimeContext(
            admin_service=SimpleNamespace(runtime=host.runtime),
            metadata={"directory_path": "root/other"},
        ),
    )
    with pytest.raises(ValueError, match="directory"):
        effect.execute(resolved_config={"session_id": "session-a"})


@pytest.mark.parametrize("host", [240], indirect=True)
def test_short_source_lifetime_fails_without_a_post(host):
    with pytest.raises(ValueError, match="five minutes"):
        post(host)
    assert not host.runtime.teams_messages.read_all()


def test_expired_uncertain_patch_reconciles_before_renewing(host, tmp_path, monkeypatch):
    graph, store, service = setup_graph(tmp_path, monkeypatch, target=TARGET)
    host.runtime.teams_messages = store
    monkeypatch.setattr(type(host.runtime), "teams_service_for_session", lambda self, sid: service)
    progress, _ = coordinator(host)
    first = progress.sync("session-a", TARGET, snapshot().model_dump(), enabled=True)
    assert first["status"] == "updated"

    def applied(req, body):
        graph.current["body"] = body["body"]
        raise httpx.ReadTimeout("Lost image edit response", request=req)

    graph.patch_outcome = applied
    next_snapshot = snapshot(2, phase="reviewing").model_dump()
    failed = progress.sync("session-a", TARGET, next_snapshot, enabled=True)
    assert failed["status"] == "notification_failed"
    assert store.get_outbound_edit_state(TARGET, first["message_id"]).pending is not None
    host.clock[0] += timedelta(seconds=601)
    graph.patch_outcome = None
    renewed = progress.sync("session-a", TARGET, next_snapshot, enabled=True)
    assert renewed["status"] == "updated" and renewed["message_revision"] == 3
    assert store.get_outbound_edit_state(TARGET, first["message_id"]).pending is None
    assert len([r for r in graph.requests if r.method == "POST"]) == 1


def test_cancellation_clears_without_file_authority_after_a_denied_candidate(host):
    progress, _ = coordinator(host)
    first = progress.sync("session-a", TARGET, snapshot().model_dump(), enabled=True)
    host.runtime.session_repository.upsert_session(
        host.session.model_copy(update={"communication_file_require_approval_for_send": True})
    )
    failed = progress.sync(
        "session-a", TARGET, snapshot(2, phase="reviewing").model_dump(), enabled=True
    )
    assert failed["status"] == "notification_failed"
    host.runtime.session_repository.upsert_session(
        host.session.model_copy(
            update={
                "allowed_tools": [ToolName.TEAMS_UPDATE_MESSAGE],
                "communication_file_send_enabled": False,
            }
        )
    )
    cleared = progress.sync(
        "session-a", TARGET, snapshot(3, phase="cancelled").model_dump(), enabled=True
    )
    assert cleared["status"] == "updated"
    assert (
        host.runtime.teams_messages.get_outbound_edit_state(TARGET, first["message_id"]).image
        is None
    )


def test_retained_png_corruption_is_a_notification_failure(host):
    progress, _ = coordinator(host)
    first = progress.sync("session-a", TARGET, snapshot().model_dump(), enabled=True)
    key = progress.journal.key("session-a", TARGET, None, "generation-a")
    state = progress.journal.read(key)
    state["png_base64"] = base64.b64encode(b"wrong").decode()
    progress.journal.write(key, state)
    host.clock[0] += timedelta(seconds=301)
    failed = progress.sync(
        "session-a",
        TARGET,
        enabled=True,
        generation_id="generation-a",
        action="renew",
        source_version=first["source_version"],
    )
    assert failed["status"] == "notification_failed" and "checksum" in failed["error"]
    assert (
        host.runtime.teams_messages.get_outbound_edit_state(TARGET, first["message_id"]).revision
        == 1
    )


def test_creation_and_edits_serialize_across_processes(host):
    import multiprocessing

    ctx = multiprocessing.get_context("fork")
    artifact = host.stage()

    def run_pair(operation):
        queue = ctx.Queue()

        def worker():
            try:
                queue.put((True, operation()))
            except Exception as exc:
                queue.put((False, type(exc).__name__))

        processes = [ctx.Process(target=worker) for _ in range(2)]
        try:
            for p in processes:
                p.start()
            for p in processes:
                p.join(10)
                if p.is_alive():
                    pytest.fail("Cross-process Teams operation exceeded ten seconds")
                assert p.exitcode == 0
            results = [queue.get(timeout=2) for _ in processes]
            assert all(ok for ok, _ in results)
            return [result for _, result in results]
        finally:
            for p in processes:
                if p.is_alive():
                    p.terminate()
                    p.join(2)
            queue.close()
            queue.join_thread()

    created = run_pair(lambda: post(host, artifact))
    assert created[0]["message_id"] == created[1]["message_id"]
    second = host.stage(snapshot(2, sections_completed=3))
    changed = run_pair(
        lambda: host.commands.update_message(
            "session-a",
            TARGET,
            created[0]["message_id"],
            "Next",
            expected_revision=1,
            image_artifact_id=second,
        )
    )
    assert {r["status"] for r in changed} == {"stubbed", "unchanged"}
    assert len(host.runtime.teams_messages.read_all()) == 1


def test_source_bytes_are_verified_after_patch_before_commit(host, tmp_path, monkeypatch):
    graph, store, service = setup_graph(tmp_path, monkeypatch, target=TARGET)
    host.runtime.teams_messages = store
    monkeypatch.setattr(type(host.runtime), "teams_service_for_session", lambda self, sid: service)
    sent = post(host)
    second = host.stage(snapshot(2, sections_completed=3))
    path = host.store.get(second).blob_path

    def corrupt(req, body):
        graph.current["body"] = body["body"]
        path.write_bytes(b"corrupted after publication")
        return httpx.Response(204)

    graph.patch_outcome = corrupt
    with pytest.raises(ValueError, match="checksum"):
        host.commands.update_message(
            "session-a", TARGET, sent["message_id"], "Next", image_artifact_id=second
        )
    assert store.get_outbound_edit_state(TARGET, sent["message_id"]).pending is not None
    assert store.get_outbound_edit_state(TARGET, sent["message_id"]).revision == 1


def test_progress_retry_cannot_commit_a_corrupted_pending_image(host, tmp_path, monkeypatch):
    import re

    graph, store, service = setup_graph(tmp_path, monkeypatch, target=TARGET)
    host.runtime.teams_messages = store
    monkeypatch.setattr(type(host.runtime), "teams_service_for_session", lambda self, sid: service)
    progress, _ = coordinator(host)
    sent = progress.sync("session-a", TARGET, snapshot().model_dump(), enabled=True)

    def corrupt(req, body):
        graph.current["body"] = body["body"]
        artifact_id = re.search(r"comm_[a-f0-9]{32}", body["body"]["content"])[0]
        host.store.get(artifact_id).blob_path.write_bytes(b"corrupted after publication")
        return httpx.Response(204)

    graph.patch_outcome = corrupt
    next_snapshot = snapshot(2, phase="reviewing").model_dump()
    failed = progress.sync("session-a", TARGET, next_snapshot, enabled=True)
    assert failed["status"] == "notification_failed"
    graph.patch_outcome = None
    retry = progress.sync("session-a", TARGET, next_snapshot, enabled=True)
    assert retry["status"] == "notification_failed" and "checksum" in retry["error"]
    state = store.get_outbound_edit_state(TARGET, sent["message_id"])
    assert state.pending is not None and state.revision == 1
    assert len([r for r in graph.requests if r.method == "POST"]) == 1


def test_failure_audit_redacts_bearer_source_urls(host, monkeypatch):
    service = host.runtime.teams_service

    def failed(*args, **kwargs):
        raise ValueError("Source failed: https://media.example/progress.png?token=private-value")

    monkeypatch.setattr(service, "post_file", failed)
    with pytest.raises(ValueError, match="Source failed"):
        post(host)
    audit = (host.runtime.config.runtime_dir / "audit.jsonl").read_text()
    assert "private-value" not in audit and "[redacted]" in audit


def test_waiting_text_edit_verifies_image_intent_created_after_its_command_read(
    host, tmp_path, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    graph, store, service = setup_graph(tmp_path, monkeypatch, target=TARGET)
    host.runtime.teams_messages = store
    monkeypatch.setattr(type(host.runtime), "teams_service_for_session", lambda self, sid: service)
    sent = post(host)
    replacement = host.stage(snapshot(2, phase="reviewing"))
    path = host.store.get(replacement).blob_path
    text_ready, image_finished = Event(), Event()
    original = service.update_message

    def wait_before_message_lock(**kwargs):
        if kwargs.get("image_version") is None:
            text_ready.set()
            assert image_finished.wait(5), "Image edit did not finish"
        return original(**kwargs)

    monkeypatch.setattr(service, "update_message", wait_before_message_lock)

    def corrupt(req, body):
        graph.current["body"] = body["body"]
        path.write_bytes(b"corrupted after publication")
        return httpx.Response(204)

    graph.patch_outcome = corrupt
    with ThreadPoolExecutor(max_workers=1) as pool:
        waiting = pool.submit(
            host.commands.update_message, "session-a", TARGET, sent["message_id"], "Next"
        )
        try:
            assert text_ready.wait(5), "Text command did not reach the service boundary"
            with pytest.raises(ValueError, match="checksum"):
                host.commands.update_message(
                    "session-a", TARGET, sent["message_id"], "Next", image_artifact_id=replacement
                )
        finally:
            image_finished.set()
        with pytest.raises(ValueError, match="checksum"):
            waiting.result(timeout=5)
    state = store.get_outbound_edit_state(TARGET, sent["message_id"])
    assert state.revision == 1 and state.pending is not None
    assert len(graph.patches) == 1


def test_progress_effect_uses_configured_timer_store_and_trigger_wake(host, tmp_path, monkeypatch):
    from evander.orchestration import internal_events
    from evander.orchestration.host_polling import wake_socket_path_for_lock

    database = tmp_path / "custom" / "workflow.db"
    lock_path = tmp_path / "custom" / "trigger.lock"
    wakes = []
    monkeypatch.setattr(
        internal_events,
        "HostWakeNotifier",
        lambda path: SimpleNamespace(notify=lambda: wakes.append(path)),
    )
    node = WorkflowNodeEnvelope.model_validate(
        {
            "id": "sync",
            "display_name": "Progress",
            "type": "effect:teams:sync-progress-message",
            "config": {"session_id": "session-a", "channel_target": TARGET, "enabled": True},
        }
    )
    effect = register_providers().effect_providers[node.type]()
    effect.initialize(
        materialize(node),
        EffectRuntimeContext(
            admin_service=SimpleNamespace(runtime=host.runtime),
            metadata={
                "directory_path": "root",
                "orchestration_store_path": str(database),
                "trigger_lock_path": str(lock_path),
            },
        ),
    )
    first = effect.execute(resolved_config={"snapshot": snapshot().model_dump()})
    second = effect.execute(
        resolved_config={"snapshot": snapshot(2, sections_completed=3).model_dump()}
    )
    assert first["status"] == "updated" and second["status"] == "coalesced"
    assert database.exists(), "Progress timers missed the configured orchestration database"
    timers = ScheduledInternalEventStore(database).list_due(host.clock[0] + timedelta(hours=1))
    assert {timer.event_type for timer in timers} == {
        "internal.teams.progress-image.renew",
        "internal.teams.progress-image.flush",
    }
    assert wakes == [wake_socket_path_for_lock(lock_path)] * 2
    default = host.runtime.config.runtime_dir / "orchestration.db"
    if default.exists():
        assert not ScheduledInternalEventStore(default).list_due(host.clock[0] + timedelta(hours=1))


@pytest.mark.parametrize("mode", ["primitives", "coordinator"])
def test_example_fragments_resolve_and_execute_one_message(host, tmp_path, monkeypatch, mode):
    from pathlib import Path
    from evander_teams_graph_addon import progress_messages

    class ClockDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return host.clock[0]

    monkeypatch.setattr(progress_messages, "datetime", ClockDateTime)
    example_target = "example-team/example-channel"
    host.runtime.session_repository.upsert_session(
        host.session.model_copy(update={"teams_channel_allowlist": [example_target]})
    )
    example = Path(__file__).resolve().parents[1] / "examples" / "progress-message" / "nodes.json"
    nodes = {node["id"]: node for node in json.loads(example.read_text())}
    assert nodes["sync"]["config"]["enabled"] is False
    assert nodes["renew"]["config"]["enabled"] is False
    prepare = {
        "coordinator_session_id": "session-a",
        "root_message_id": "root-a",
        "progress_snapshot": snapshot().model_dump(),
        "progress_creation_key": "example-creation",
    }
    upstream = {"prepare": prepare}
    database = tmp_path / "configured" / "events.db"
    registration = register_providers()

    def execute(node_id, **overrides):
        node = WorkflowNodeEnvelope.model_validate(nodes[node_id])
        binding = resolve_workflow_bindings(
            node.config, upstream, {key: set(value) for key, value in upstream.items()}
        )
        assert binding.trace.consumed_output_keys, "Example bindings were not resolved"
        effect = registration.effect_providers[node.type]()
        effect.initialize(
            materialize(node),
            EffectRuntimeContext(
                admin_service=SimpleNamespace(runtime=host.runtime),
                metadata={"directory_path": "root", "orchestration_store_path": str(database)},
            ),
        )
        result = effect.execute(resolved_config={**binding.resolved_config, **overrides})
        upstream[node_id] = result
        return result

    if mode == "primitives":
        execute("render")
        sent = execute("post")
        assert execute("post")["message_id"] == sent["message_id"]
        prepare.update(
            progress_message_id=sent["message_id"],
            progress_message_revision=sent["message_revision"],
        )
        prepare["progress_snapshot"] = snapshot(2, phase="reviewing").model_dump()
        execute("render")
        updated = execute("update")
        assert updated["message_id"] == sent["message_id"] and updated["message_revision"] == 2
    else:
        first = execute("sync", enabled=True)
        assert first["status"] == "updated" and first["message_revision"] == 1
        prepare["progress_snapshot"] = snapshot(2, sections_completed=3).model_dump()
        assert execute("sync", enabled=True)["status"] == "coalesced"

        def fire_timer(action, source):
            timers = ScheduledInternalEventStore(database).list_due(
                host.clock[0] + timedelta(hours=1)
            )
            timer = next(
                t
                for t in timers
                if t.payload["action"] == action and t.payload["source_version"] == source
            )
            upstream["timer"] = event_trigger_payload(
                InternalEvent(
                    event_id=timer.schedule_id, event_type=timer.event_type, payload=timer.payload
                )
            )
            return execute("renew", enabled=True)

        host.clock[0] += timedelta(seconds=31)
        flushed = fire_timer("flush", first["source_version"])
        assert flushed["message_revision"] == 2
        state = host.runtime.teams_messages.get_outbound_edit_state(
            example_target, first["message_id"]
        )
        host.clock[0] = state.image.renew_at + timedelta(seconds=1)
        renewed = fire_timer("renew", flushed["source_version"])
        assert renewed["message_revision"] == 3 and renewed["message_id"] == first["message_id"]
        assert execute("renew", enabled=True)["status"] == "unchanged"
        prepare["progress_snapshot"] = snapshot(3, phase="cancelled").model_dump()
        final = execute("sync", enabled=True)
        assert final["message_revision"] == 4 and final["source_version"] is None
        assert (
            host.runtime.teams_messages.get_outbound_edit_state(
                example_target, first["message_id"]
            ).image
            is None
        )
    assert len(host.runtime.teams_messages.read_all()) == 1

from __future__ import annotations


import asyncio


import base64


import io


import json


from concurrent.futures import ThreadPoolExecutor


from datetime import UTC, datetime, timedelta


import httpx


import pytest


from PIL import Image


from mcp.server.fastmcp.exceptions import ToolError


from evander.config import GatewayConfig


from evander.directories import DirectoryService


from evander.gateway import create_app, create_server


from evander.models import SessionManifest, ToolName


from evander.runtime import build_runtime


from evander.voice_sessions import VoiceQueryDelegator


from evander.voice_visuals import MAX_IMAGE_BYTES, MAX_REQUEST_BYTES, VoiceVisualStore


from gateway_tool_support import call_tool


TENANT = "11111111-1111-4111-8111-111111111111"


CALLER = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def context(tmp_path, monkeypatch):
    runtime = build_runtime(
        GatewayConfig(
            runtime_dir=tmp_path,
            audit_log_path=tmp_path / "audit.jsonl",
            session_store_path=tmp_path / "sessions.json",
            approval_store_path=tmp_path / "approvals.json",
            draft_store_path=tmp_path / "drafts.json",
            email_store_path=tmp_path / "emails.json",
            discord_command_store_path=tmp_path / "discord.json",
        )
    )
    DirectoryService(runtime.directories).create(parent="root", name="personal")
    runtime.provider_config.config_store.write(
        "root",
        domain="providers",
        payload={
            "teams_call_enabled": True,
            "teams_call_visual_context_enabled": True,
            "teams_delegated_tenant_id": TENANT,
            "teams_call_routes": [
                {
                    "tenant_id": TENANT,
                    "caller_user_id": CALLER,
                    "directory_path": "root/personal",
                    "origin_session_id": "assistant-a",
                }
            ],
        },
    )
    for name in ("assistant-a", "assistant-b"):
        runtime.session_repository.upsert_session(
            SessionManifest(
                session_id=name,
                directory_path="root/personal",
                model="gpt-6-sol",
                expires_at=datetime.now(UTC) + timedelta(days=1),
                teams_user_allowlist=[f"user:{CALLER}"],
                allowed_tools=[
                    ToolName.SUBMIT_VOICE_QUERY_RESPONSE,
                    ToolName.GET_VOICE_QUERY_VISUAL_CONTEXT,
                ],
            )
        )
    monkeypatch.setenv("VOICE_MEDIA_CONTROLLER_TOKEN", "synthetic-controller-token")
    return runtime


def admit(context, call_id="call-1"):
    response = context.teams_voice_service.admit(
        {
            "tenant_id": TENANT,
            "caller_user_id": CALLER,
            "graph_call_id": call_id,
        }
    )
    return context.voice_session_store.get_by_voice_session_id(response["voice_session_id"])


def image(source="screen", color="red"):
    output = io.BytesIO()
    Image.new("RGB", (16, 12), color).save(output, format="JPEG")
    return (
        {"source": source, "captured_at": datetime.now(UTC).isoformat(), "width": 16, "height": 12},
        output.getvalue(),
    )


def create_query(context, call=None, images=None, request_id="function-1"):
    return context.teams_voice_service.create_visual_query(
        call or admit(context),
        {
            "question": "Explain the shared screen",
            "request_id": request_id,
            "metadata": {"reason": "visual work"},
        },
        images or [image()],
    )


def test_visuals_default_off_and_directory_override_keeps_admitted_binding(context):
    assert not GatewayConfig().teams_call_visual_context_enabled
    first = admit(context)
    assert first.caller_metadata["visual_context"]["enabled"]
    context.provider_config.config_store.write(
        "root/personal",
        domain="providers",
        payload={
            "teams_call_visual_context_enabled": False,
        },
    )
    assert admit(context).caller_metadata["visual_context"]["enabled"]
    context.voice_session_store.update_from_bridge_payload(
        first.voice_session_id, {"status": "ended"}
    )
    second = admit(context, "call-2")
    assert not second.caller_metadata["visual_context"]["enabled"]
    with pytest.raises(ValueError, match="not enabled"):
        create_query(context, second)


def test_removing_root_visual_opt_in_disables_next_call_without_restart(context):
    running = build_runtime(context.provider_config.resolve_for_directory("root"))
    assert running.config.teams_call_visual_context_enabled is True
    providers = running.provider_config.config_store.read("root", domain="providers")
    del providers["teams_call_visual_context_enabled"]
    running.provider_config.config_store.write("root", domain="providers", payload=providers)

    call = admit(running)
    assert call.caller_metadata["visual_context"]["enabled"] is False
    with pytest.raises(ValueError, match="not enabled"):
        create_query(running, call)


@pytest.mark.parametrize("change", ["grant", "approval", "model"])
def test_missing_visual_capability_preserves_audio_admission(context, change):
    if change == "model":
        context.provider_config.config_store.write(
            "root/personal",
            domain="providers",
            payload={
                "openai_realtime_model": "gpt-4o-realtime-preview",
            },
        )
    else:
        session = context.session_repository.get_session("assistant-a")
        if change == "grant":
            session.allowed_tools = [ToolName.SUBMIT_VOICE_QUERY_RESPONSE]
        else:
            session.approval_required_tools = [ToolName.GET_VOICE_QUERY_VISUAL_CONTEXT]
        context.session_repository.upsert_session(session)
    call = admit(context)
    assert not call.caller_metadata["visual_context"]["enabled"]
    assert call.origin_session_id == "assistant-a"


def test_snapshots_persist_before_wake_and_retry_does_not_duplicate(context, monkeypatch):
    call = admit(context)
    images = [image("camera"), image("screen")]
    schedule = context.wake_store.schedule_resume_request

    def inspected_schedule(**kwargs):
        query = context.voice_session_store.get_query(kwargs["metadata"]["query_id"])
        stored = context.voice_visual_store.read(query)
        assert {ref.source for ref, _ in stored} == {"camera", "screen"}
        assert [content for _, content in stored] == [content for _, content in images]
        return schedule(**kwargs)

    monkeypatch.setattr(context.wake_store, "schedule_resume_request", inspected_schedule)
    with ThreadPoolExecutor(max_workers=4) as executor:
        queries = list(executor.map(lambda _: create_query(context, call, images), range(4)))
    assert len({query.query_id for query in queries}) == 1
    assert len(context.wake_store.list_requests(session_id="assistant-a")) == 1
    assert len(list(context.voice_visual_store.root.glob("*.jpg"))) == 2
    with pytest.raises(ValueError, match="different visual context"):
        create_query(context, call, [image("screen", "blue")])


@pytest.mark.parametrize("backend", ["libvirt_vm", "container_worker_vm"])
def test_image_tool_delivers_to_original_assistant_without_workspace_copy(context, backend):
    providers = context.provider_config.config_store.read("root", domain="providers")
    context.provider_config.config_store.write(
        "root", domain="providers", payload={**providers, "execution_backend": backend}
    )
    images = [image()]
    query = create_query(context, images=images)
    server = create_server(context)
    result = asyncio.run(
        server.call_tool(
            "get_voice_query_visual_context",
            {
                "session_id": "assistant-a",
                "query_id": query.query_id,
            },
        )
    )
    content = result.content
    assert len(content) == 2
    assert content[1].type == "image" and base64.b64decode(content[1].data) == images[0][1]
    metadata = json.loads(content[0].text)
    assert metadata["source"] == "screen" and metadata["width"] == 16
    with pytest.raises(ToolError, match="outside this session scope"):
        asyncio.run(
            server.call_tool(
                "get_voice_query_visual_context",
                {
                    "session_id": "assistant-b",
                    "query_id": query.query_id,
                },
            )
        )
    encoded = base64.b64encode(images[0][1]).decode()
    for path in (
        context.config.audit_log_path,
        context.voice_session_store.path,
        context.wake_store.path,
    ):
        assert encoded.encode() not in path.read_bytes()
        wal = path.with_name(path.name + "-wal")
        if wal.exists():
            assert encoded.encode() not in wal.read_bytes()
    assert list(context.config.runtime_dir.glob("**/communication-artifacts/*.bin")) == []
    asyncio.run(
        server.call_tool(
            "submit_voice_query_response",
            {
                "session_id": "assistant-a",
                "query_id": query.query_id,
                "answer": "The screen shows the supplied content",
            },
        )
    )
    assert list(context.voice_visual_store.root.glob("*.jpg")) == []


def test_hangup_and_voice_timeout_preserve_pending_images_but_expiry_removes_them(context):
    call = admit(context)
    query = create_query(context, call)
    context.voice_session_store.mark_query_timeout(query.query_id)
    context.voice_session_store.update_from_bridge_payload(
        call.voice_session_id, {"status": "ended"}
    )
    context.voice_visual_store.cleanup(get_query=context.voice_session_store.get_query)
    assert (
        len(
            context.teams_voice_service.read_visual_context(
                query_id=query.query_id, origin_session_id="assistant-a"
            )
        )
        == 1
    )
    context.voice_visual_store.cleanup(now=datetime.now(UTC) + timedelta(hours=25))
    with pytest.raises(FileNotFoundError, match="expired"):
        context.teams_voice_service.read_visual_context(
            query_id=query.query_id, origin_session_id="assistant-a"
        )


def test_quota_failure_wakes_with_explicit_missing_context_and_no_partial_images(
    context, monkeypatch
):
    call = admit(context)
    images = [image()]
    store = VoiceVisualStore(context.config.runtime_dir, global_quota=len(images[0][1]))
    monkeypatch.setattr(type(context), "voice_visual_store", property(lambda _: store))
    first = create_query(context, call, images)
    second = create_query(context, call, images, request_id="function-2")
    assert first.visual_context and not second.visual_context
    assert "storage is full" in second.visual_context_unavailable
    assert len(context.wake_store.list_requests(session_id="assistant-a")) == 2
    assert len(list(store.root.glob("*.jpg"))) == 1


@pytest.mark.parametrize(
    "invalid", ["bytes", "size", "dimensions", "source", "duplicate", "timestamp"]
)
def test_invalid_snapshots_are_rejected_before_query_or_wake(context, invalid):
    call = admit(context)
    metadata, content = image()
    images = [(metadata, content)]
    if invalid == "bytes":
        images = [(metadata, b"not an image")]
    elif invalid == "size":
        images = [(metadata, b"x" * (MAX_IMAGE_BYTES + 1))]
    elif invalid == "dimensions":
        metadata["width"] = 17
    elif invalid == "source":
        metadata["source"] = "another-caller"
    elif invalid == "duplicate":
        images *= 2
    else:
        metadata["captured_at"] = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    with pytest.raises(ValueError):
        create_query(context, call, images)
    assert context.voice_session_store.list_queries_for_voice_session(call.voice_session_id) == []
    assert context.wake_store.list_requests(session_id="assistant-a") == []


def test_restart_cleanup_removes_orphan_blobs_and_private_files(context):
    query = create_query(context)
    store = context.voice_visual_store
    (store.root / "interrupted.jpg").write_bytes(b"partial image")
    restarted = VoiceVisualStore(context.config.runtime_dir)
    restarted.cleanup(get_query=context.voice_session_store.get_query)
    assert not (store.root / "interrupted.jpg").exists()
    assert len(restarted.read(query)) == 1
    assert store.root.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in store.root.glob("*.jpg"))


def test_interrupted_attachment_cannot_dispatch_without_images_and_retry_recovers(
    context, monkeypatch
):
    call = admit(context)
    images = [image()]
    store = context.voice_visual_store
    stage = store.stage
    monkeypatch.setattr(type(context), "voice_visual_store", property(lambda _: store))

    def interrupted(query, snapshots):
        stage(query, snapshots)
        raise OSError("Simulated interruption after blob commit")

    with monkeypatch.context() as patch:
        patch.setattr(store, "stage", interrupted)
        with pytest.raises(OSError):
            create_query(context, call, images)
    assert context.wake_store.list_requests(session_id="assistant-a") == []
    with pytest.raises(ValueError, match="different visual context"):
        VoiceQueryDelegator(
            store=context.voice_session_store, wake_store=context.wake_store
        ).create_query_and_wake_origin(
            call.voice_session_id,
            question="Explain the shared screen",
            request_id="function-1",
        )
    query = create_query(context, call, images)
    assert len(context.wake_store.list_requests(session_id="assistant-a")) == 1
    assert len(list(store.root.glob("*.jpg"))) == 1
    assert store.read(query)[0][1] == images[0][1]


def test_unattached_committed_bundle_is_removed_after_restart_grace_period(context, monkeypatch):
    call = admit(context)
    images = [image()]
    with monkeypatch.context() as patch:
        patch.setattr(
            context.voice_session_store,
            "attach_query_visual_context",
            lambda *a, **kw: (_ for _ in ()).throw(OSError("Interrupted")),
        )
        with pytest.raises(OSError):
            create_query(context, call, images)
    restarted = VoiceVisualStore(context.config.runtime_dir)
    restarted.cleanup(get_queries=context.voice_session_store.get_queries)
    assert len(list(restarted.root.glob("*.jpg"))) == 1
    restarted.cleanup(
        get_queries=context.voice_session_store.get_queries,
        now=datetime.now(UTC) + timedelta(minutes=6),
    )
    assert list(restarted.root.glob("*.jpg")) == []


def test_parallel_uploads_cannot_overrun_global_quota(context, monkeypatch):
    call = admit(context)
    images = [image()]
    store = VoiceVisualStore(context.config.runtime_dir, global_quota=2 * len(images[0][1]))
    monkeypatch.setattr(type(context), "voice_visual_store", property(lambda _: store))
    with ThreadPoolExecutor(max_workers=6) as executor:
        queries = list(
            executor.map(
                lambda index: create_query(context, call, images, f"function-{index}"), range(6)
            )
        )
    assert sum(bool(query.visual_context) for query in queries) == 2
    assert sum(bool(query.visual_context_unavailable) for query in queries) == 4
    assert sum(path.stat().st_size for path in store.root.glob("*.jpg")) == 2 * len(images[0][1])


def test_call_quota_does_not_consume_another_calls_budget(context, monkeypatch):
    call = admit(context)
    images = [image()]
    store = VoiceVisualStore(context.config.runtime_dir, call_quota=len(images[0][1]))
    monkeypatch.setattr(type(context), "voice_visual_store", property(lambda _: store))
    first = create_query(context, call, images)
    blocked = create_query(context, call, images, "function-2")
    context.voice_session_store.update_from_bridge_payload(
        call.voice_session_id, {"status": "ended"}
    )
    other = create_query(context, admit(context, "call-2"), images, "function-3")
    assert first.visual_context and other.visual_context and not blocked.visual_context
    assert store.read(first)[0][1] == images[0][1]


def test_cleanup_failure_does_not_lose_a_confirmed_answer(context, monkeypatch):
    query = create_query(context)
    store = context.voice_visual_store
    monkeypatch.setattr(type(context), "voice_visual_store", property(lambda _: store))
    with monkeypatch.context() as patch:
        patch.setattr(
            store,
            "cleanup",
            lambda **kw: (_ for _ in ()).throw(OSError("Temporary unlink failure")),
        )
        result = call_tool(
            create_server(context),
            "submit_voice_query_response",
            {
                "session_id": "assistant-a",
                "query_id": query.query_id,
                "answer": "Image inspected",
            },
        )
        assert result["status"] == "answered"
    assert context.voice_session_store.get_query(query.query_id).status == "answered"
    assert list(store.root.glob("*.jpg"))
    assert context.teams_voice_service.cleanup_visual_context()
    assert list(store.root.glob("*.jpg")) == []


def test_multipart_http_upload_is_authenticated_and_bounded(context):
    call = admit(context)
    metadata, content = image()
    payload = {
        "question": "Read the shared screen",
        "request_id": "http-query",
        "images": [metadata],
    }

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(context)), base_url="http://test"
        ) as client:
            path = f"/voice-sessions/{call.voice_session_id}/queries"
            parts = {
                "payload": (None, json.dumps(payload), "application/json"),
                "screen": ("screen.jpg", content, "image/jpeg"),
            }
            assert (await client.post(path, files=parts)).status_code == 401
            headers = {"Authorization": "Bearer synthetic-controller-token"}
            response = await client.post(path, headers=headers, files=parts)
            assert response.status_code == 201, response.text
            replay = await client.post(path, headers=headers, files=parts)
            assert replay.json()["query_id"] == response.json()["query_id"]
            oversized = await client.post(
                path,
                headers={**headers, "Content-Type": "multipart/form-data; boundary=example"},
                content=b"x" * (MAX_REQUEST_BYTES + 1),
            )
            assert oversized.status_code == 400
            return response.json()

    result = asyncio.run(run())
    assert len(result["visual_context"]) == 1
    query = context.voice_session_store.get_query(result["query_id"])
    assert context.voice_visual_store.read(query)[0][1] == content


@pytest.mark.parametrize("invalid", ["parts", "mime", "metadata", "source", "disabled"])
def test_invalid_multipart_never_schedules_work(context, invalid):
    if invalid == "disabled":
        context.provider_config.config_store.write(
            "root/personal",
            domain="providers",
            payload={
                "teams_call_visual_context_enabled": False,
            },
        )
    call = admit(context)
    metadata, content = image()
    payload = {"question": "Read the screen", "request_id": "bad-http", "images": [metadata]}
    if invalid == "metadata":
        payload["metadata"] = ["invalid object"]
    if invalid == "source":
        payload["images"][0]["source"] = ["invalid type"]
    parts = [
        ("payload", (None, json.dumps(payload), "application/json")),
        ("screen", ("screen.jpg", content, "image/png" if invalid == "mime" else "image/jpeg")),
    ]
    if invalid == "parts":
        parts += [("camera", ("extra.jpg", content, "image/jpeg"))] * 2

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(context)), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/voice-sessions/{call.voice_session_id}/queries",
                files=parts,
                headers={"Authorization": "Bearer synthetic-controller-token"},
            )
            assert response.status_code == 400, response.text

    asyncio.run(run())
    assert context.voice_session_store.list_queries_for_voice_session(call.voice_session_id) == []
    assert context.wake_store.list_requests(session_id="assistant-a") == []

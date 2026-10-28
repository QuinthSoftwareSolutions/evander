import asyncio
import threading
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from evander.admin.web.workflow import workflow_routes, workflow_run_events


@pytest.mark.parametrize("path", ["document", "catalog", "runs", "stream", "stream_route"])
def test_workflow_read_and_serialization_leave_event_loop(path):
    loop_thread = threading.get_ident()
    calls = []

    def assert_worker(label):
        assert threading.get_ident() != loop_thread
        calls.append(label)

    class Snapshot:
        graphs = []
        workflow_packs = []
        runs = []
        node_activity = {}
        recent_node_activity = []

        def model_dump(self, **kwargs):
            assert_worker("serialize")
            return {"graphs": [], "runs": []}

    def read(*args, **kwargs):
        assert_worker("read")
        return Snapshot()

    def journal():
        assert_worker("journal")

        def cursor():
            assert_worker("cursor")
            return 0

        return SimpleNamespace(cursor=cursor)

    service = SimpleNamespace(
        workflow_config_admin=SimpleNamespace(
            read_composed_workflow_editor_document=read,
            read_workflow_editor_catalog=read,
            read_workflow_run_snapshot=read,
            workflow_event_journal=journal,
        ),
        teams_admin=SimpleNamespace(normalize_directory_reference=lambda value: value),
    )
    auth = SimpleNamespace(
        can=lambda *args, **kwargs: True, require_permission=lambda *args, **kwargs: None
    )

    async def receive():
        await asyncio.Event().wait()

    async def exercise():
        request = Request({"type": "http", "method": "GET", "query_string": b""}, receive)
        if path == "stream":
            stream = workflow_run_events(service, request)
            try:
                assert (await anext(stream)).startswith("event: snapshot")
            finally:
                await stream.aclose()
        else:
            suffix = "runs/stream" if path == "stream_route" else path
            route = next(
                route
                for route in workflow_routes(service, auth)
                if route.path == f"/api/workflow-editor/{suffix}" and "GET" in route.methods
            )
            response = await route.endpoint(request)
            assert response.status_code == 200
            if path == "stream_route":
                try:
                    assert (await anext(response.body_iterator)).startswith("event: snapshot")
                finally:
                    await response.body_iterator.aclose()

    asyncio.run(exercise())
    assert "read" in calls
    assert "serialize" in calls
    if path.startswith("stream"):
        assert "journal" in calls
        assert "cursor" in calls

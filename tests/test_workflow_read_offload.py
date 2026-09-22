import asyncio
import threading
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from evander.admin.web.workflow import workflow_routes, workflow_run_events


@pytest.mark.parametrize("path", ["document", "catalog", "runs", "stream"])
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

    service = SimpleNamespace(
        workflow_config_admin=SimpleNamespace(
            read_composed_workflow_editor_document=read,
            read_workflow_editor_catalog=read,
            read_workflow_run_snapshot=read,
            workflow_event_journal=lambda: SimpleNamespace(cursor=lambda: 0),
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
            route = next(
                route
                for route in workflow_routes(service, auth)
                if route.path == f"/api/workflow-editor/{path}" and "GET" in route.methods
            )
            assert (await route.endpoint(request)).status_code == 200

    asyncio.run(exercise())
    assert "read" in calls
    assert "serialize" in calls

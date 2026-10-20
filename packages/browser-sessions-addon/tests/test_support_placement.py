from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from evander.admin.services.container_worker_assignment_service import (
    ContainerWorkerAssignmentService,
)
from evander.config import GatewayConfig
from evander.models import SessionManifest
from evander_browser_sessions_addon.models import Session, Settings
from evander_browser_sessions_addon.service import Bindings, Parent


@pytest.mark.parametrize("named_worker", [False, True])
def test_local_support_respects_core_worker_selection(tmp_path, named_worker):
    """Browser supervisor labels must never become core worker registry IDs."""
    config = GatewayConfig(
        runtime_dir=tmp_path,
        session_store_path=tmp_path / "sessions.json",
        container_worker_base_url="http://default-worker.test:11888",
        container_worker_token_env="CORE_WORKER_TOKEN",
        container_workers={
            "named-worker": {
                "base_url": "http://named-worker.test:11888",
                "token_env": "NAMED_WORKER_TOKEN",
            }
        }
        if named_worker
        else {},
    )
    expires = datetime.now(UTC) + timedelta(hours=1)
    owner = SessionManifest(
        session_id="author",
        directory_path="root/demo",
        allowed_tools=["browser_session_create"],
        expires_at=expires,
        model="test-model",
    )
    created = []

    def create(payload, *, capability_restriction):
        placement = ContainerWorkerAssignmentService(config).resolve(
            payload["session_id"],
            directory_path=payload["directory_path"],
            worker_id=payload.get("worker_id"),
            persist=False,
        )
        child = capability_restriction.apply(
            owner.model_copy(update={"session_id": payload["session_id"]})
        )
        created.append((placement, child))
        return {"success": True}

    core = SimpleNamespace(
        session_repository=SimpleNamespace(get_session=lambda _: None),
        policy=SimpleNamespace(get_session=lambda _: owner),
    )
    bindings = Bindings(
        tmp_path,
        SimpleNamespace(
            runtime=core,
            create_admin_agent=create,
            enqueue_agent_prompt=lambda *args, **kwargs: None,
        ),
    )
    session = Session(
        id="browser-local",
        directory="root/demo",
        agent_session_id="author",
        support_agent_session_id="browser-support-local",
        mode="user_hosted",
        created_at=datetime.now(UTC).timestamp(),
        expires_at=expires.timestamp(),
        last_activity_at=datetime.now(UTC).timestamp(),
    )
    parent = Parent(
        "author",
        "root/demo",
        expires.timestamp(),
        worker_id="named-worker" if named_worker else None,
    )
    settings = Settings(
        workers={
            "browser-supervisor": {
                "base_url": "http://browser-worker.test:11891",
                "parent_worker_url": "http://default-worker.test:11888",
                "token_env": "BROWSER_WORKER_TOKEN",
            }
        }
    )
    assert bindings.prepare_support(session, parent, settings) == "browser-support-local"
    assert len(created) == 1
    placement, child = created[0]
    expected = (
        "http://named-worker.test:11888" if named_worker else "http://default-worker.test:11888"
    )
    assert placement.container_worker_base_url == expected
    assert set(child.allowed_tools) == {
        "browser_session_status",
        "browser_session_observe",
        "browser_session_act",
        "browser_session_highlight",
        "browser_session_reply",
        "browser_session_save_screenshot",
    }
    assert child.metadata["capability_binding"] == {
        "kind": "browser_support",
        "browser_session_id": "browser-local",
    }

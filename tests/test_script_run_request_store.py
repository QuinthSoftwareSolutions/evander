from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_sandbox.repositories.script_run_requests import ScriptRunRequestStore


def test_enqueue_persists_expected_shape(tmp_path: Path) -> None:
    store = ScriptRunRequestStore(tmp_path / "script-run-requests-store.json")

    queued = store.enqueue(
        session_id="session-1",
        directory_path="root/team-a",
        script_name="deploy-review",
        parameters=["--pr", "42"],
        request_id="script-1",
    )

    assert queued.request_id == "script-1"
    payload = json.loads((tmp_path / "script-run-requests-store.json").read_text(encoding="utf-8"))
    assert payload["requests"]["script-1"]["session_id"] == "session-1"
    assert payload["requests"]["script-1"]["directory_path"] == "root/team-a"
    assert payload["requests"]["script-1"]["script_name"] == "deploy-review"
    assert payload["requests"]["script-1"]["parameters"] == ["--pr", "42"]


def test_enqueue_rejects_duplicate_request_id(tmp_path: Path) -> None:
    store = ScriptRunRequestStore(tmp_path / "script-run-requests-store.json")
    store.enqueue(
        session_id="session-1",
        directory_path="root",
        script_name="deploy-review",
        parameters=[],
        request_id="script-1",
    )

    with pytest.raises(ValueError, match="Script run request already exists: script-1"):
        store.enqueue(
            session_id="session-1",
            directory_path="root",
            script_name="deploy-review",
            parameters=[],
            request_id="script-1",
        )

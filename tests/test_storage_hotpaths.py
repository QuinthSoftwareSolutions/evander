from __future__ import annotations

import json
import os
import stat
import weakref
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from evander.models import Directory
from evander.pr_feedback.store import FeedbackEnvelope, PrFeedbackStore
from evander.repositories.directories import ReadOnlyJsonDirectoryRepository
from evander.storage import write_json_atomically


def test_directory_snapshot_looks_up_only_requested_model_and_isolates_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "directories.json"
    directories = {
        f"directory-{i}": Directory(
            id=f"directory-{i}", name=f"team-{i}", metadata={"policy": {"tools": ["read"]}}
        ).model_dump(mode="json")
        for i in range(1000)
    }
    path.write_text(json.dumps(directories), encoding="utf-8")
    snapshot = ReadOnlyJsonDirectoryRepository(path)
    copied = []
    original_copy = Directory.model_copy

    def counted_copy(self, *args, **kwargs):
        copied.append(self.id)
        return original_copy(self, *args, **kwargs)

    monkeypatch.setattr(Directory, "model_copy", counted_copy)
    found = snapshot.get("directory-999")
    assert found is not None
    assert found.name == "team-999"
    assert copied == ["directory-999"]
    found.metadata["policy"]["tools"].append("write")
    assert snapshot.get("directory-999").metadata == {"policy": {"tools": ["read"]}}
    copied.clear()
    assert snapshot.get("missing") is None
    assert copied == []
    # Later publication does not alter an existing read-only policy snapshot.
    path.write_text("{}", encoding="utf-8")
    assert snapshot.get("directory-999") is not None
    assert ReadOnlyJsonDirectoryRepository(path).get("directory-999") is None


def test_feedback_pages_bound_live_models_and_keep_filters_and_timestamp_ties(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = PrFeedbackStore(tmp_path / "feedback.json")
    stamp = datetime(2026, 10, 1, tzinfo=UTC)

    def row(feedback_id, seconds, *, repo="project/repo"):
        return {
            "feedback_id": feedback_id,
            "provider": "github",
            "repo": repo,
            "pr_id": "17",
            "author": "reviewer",
            "body": "Please review this change",
            "created_at": (stamp + timedelta(seconds=seconds)).isoformat(),
        }

    rows = {
        "ignored": row("ignored", 0.5),
        "unrelated": row("unrelated", 0.5, repo="other/repo"),
        "consumed": row("consumed", 0),
        "first-tie": row("first-tie", 1),
        "second-tie": row("second-tie", 1),
        **{f"feedback-{i}": row(f"feedback-{i}", i + 1) for i in range(3000, 0, -1)},
        "malformed": None,
    }
    monkeypatch.setattr(store, "_read_raw", lambda: {"feedback": rows})
    live = peak = validated = 0
    original_validate = FeedbackEnvelope.model_validate

    def released():
        nonlocal live
        live -= 1

    def counted_validate(cls, *args, **kwargs):
        nonlocal live, peak, validated
        model = original_validate(*args, **kwargs)
        validated += 1
        live += 1
        peak = max(peak, live)
        weakref.finalize(model, released)
        return model

    monkeypatch.setattr(FeedbackEnvelope, "model_validate", classmethod(counted_validate))
    page = store.list_feedback_for_streams(
        streams=[{"provider": "github", "repo": "project/repo", "pr_id": "17"}],
        after={("github", "project/repo", "17"): stamp.isoformat()},
        exclude_feedback_ids={"ignored"},
        limit=3,
    )
    assert [item.feedback_id for item in page] == ["first-tie", "second-tie", "feedback-1"]
    assert validated == 3005
    assert peak <= 5
    # The same bounded path powers provider-filtered global pages.
    del page
    rows["other-provider"] = {**row("other-provider", -1), "provider": "local"}
    page = store.list_all_feedback(allowed_providers={"github"}, limit=1)
    assert [item.feedback_id for item in page] == ["consumed"]


@pytest.mark.parametrize(
    "limit,expected", [(None, ["first", "second", "third"]), (0, []), (-1, ["first", "second"])]
)
def test_feedback_unbounded_and_nonpositive_limits_keep_slice_behavior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit, expected
) -> None:
    store = PrFeedbackStore(tmp_path / "feedback.json")
    payload = {
        "feedback": {
            name: {
                "feedback_id": name,
                "provider": "local",
                "repo": "repo",
                "pr_id": "17",
                "author": "reviewer",
                "body": "feedback",
                "created_at": f"2026-10-0{day}T00:00:00Z",
            }
            for name, day in (("third", 3), ("first", 1), ("second", 2))
        }
    }
    monkeypatch.setattr(store, "_read_raw", lambda: payload)
    assert [row.feedback_id for row in store.list_all_feedback(limit=limit)] == expected


def test_bounded_feedback_still_validates_rows_beyond_the_selected_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pydantic import ValidationError

    store = PrFeedbackStore(tmp_path / "feedback.json")
    rows = {
        "valid": {
            "feedback_id": "valid",
            "provider": "local",
            "repo": "repo",
            "pr_id": "17",
            "author": "reviewer",
            "body": "feedback",
            "created_at": "2026-10-01T00:00:00Z",
        },
        "invalid": {"feedback_id": "invalid"},
    }
    monkeypatch.setattr(store, "_read_raw", lambda: {"feedback": rows})
    with pytest.raises(ValidationError):
        store.list_all_feedback(limit=1)


def test_atomic_json_streams_formatted_chunks_to_private_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "state.json"
    payload = {"z": ["non-ascii \u00e5", 12, None], "a": {"text": ["x" * 100] * 2000}}
    expected = json.dumps(payload, indent=2, sort_keys=True)
    original_fdopen = os.fdopen
    write_sizes = []

    class RecordingFile:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            self.handle.__enter__()
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.handle, name)

        def write(self, text):
            assert stat.S_IMODE(os.fstat(self.handle.fileno()).st_mode) == 0o600
            assert not path.exists()
            write_sizes.append(len(text))
            return self.handle.write(text)

    monkeypatch.setattr(
        os, "fdopen", lambda *args, **kwargs: RecordingFile(original_fdopen(*args, **kwargs))
    )
    write_json_atomically(path, payload, file_mode=0o640)
    assert write_sizes
    assert max(write_sizes) < len(expected) // 10
    assert path.read_text(encoding="utf-8") == expected
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert list(tmp_path.glob(".state.json.*.tmp")) == []


def test_atomic_json_serialization_failure_retains_published_file_and_removes_temp(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.json"
    write_json_atomically(path, {"revision": 1}, file_mode=0o640)
    previous = path.read_bytes()
    with pytest.raises(TypeError):
        write_json_atomically(path, {"already_written": ["a"] * 1000, "unsupported": object()})
    assert path.read_bytes() == previous
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert list(tmp_path.glob(".state.json.*.tmp")) == []

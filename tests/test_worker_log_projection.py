import json
import random
from datetime import UTC, datetime

import pytest

from evander.worker import service
from evander.worker.log_projection import (
    JsonlProjectionCache,
    merge_tail_log_snapshot,
    read_log_tail,
)


def test_projection_only_consumes_appended_records_and_rebuilds_on_rewrite(tmp_path):
    path = tmp_path / "events.jsonl"
    cache = JsonlProjectionCache()
    calls = []

    def consume(state, event):
        calls.append(event)
        state.append(event)

    def read():
        return cache.read("test", [path], factory=list, consume=consume, weight=lambda state: 64)

    path.write_text("1\n2\n")
    assert read() == [1, 2]
    assert read() == [1, 2]
    assert calls == [1, 2]
    with path.open("a") as output:
        output.write("3\n")
    assert read() == [1, 2, 3]
    assert calls == [1, 2, 3]
    # In-place rewrite of equal size must invalidate despite unchanged inode.
    path.write_text("4\n5\n6\n")
    assert read() == [4, 5, 6]
    replacement = tmp_path / "replacement"
    replacement.write_text("7\n")
    replacement.replace(path)
    assert read() == [7]
    path.write_text("8\n9\n10\n")
    assert read() == [8, 9, 10]
    path.unlink()
    assert read() == []


def test_projection_provisional_final_line_and_partial_utf8(tmp_path):
    path = tmp_path / "events.jsonl"
    cache = JsonlProjectionCache()

    def read():
        return cache.read(
            "test",
            [path],
            factory=list,
            consume=lambda state, event: state.append(event),
            weight=lambda state: 64,
        )

    path.write_text("1\n2")
    assert read() == [1, 2]
    with path.open("a") as output:
        output.write('0\n{"message":"')
    assert read() == [1, 20]
    with path.open("ab") as output:
        output.write("å".encode()[:1])
    assert read() == [1, 20]
    with path.open("ab") as output:
        output.write("å".encode()[1:] + b'"}\n')
    result = read()
    assert result == [1, 20, {"message": "å"}]
    result[-1]["message"] = "caller mutation"
    assert read()[-1] == {"message": "å"}


def test_projection_rebuilds_changed_earlier_file_and_bounds_entries(tmp_path):
    paths = [tmp_path / "one", tmp_path / "two"]
    paths[0].write_text("1\n")
    paths[1].write_text("2\n")
    cache = JsonlProjectionCache(max_entries=2, max_bytes=128)

    def read(key):
        return cache.read(
            key,
            paths,
            factory=list,
            consume=lambda state, event: state.append(event),
            weight=lambda state: 64,
        )

    assert read("first") == [1, 2]
    with paths[0].open("a") as output:
        output.write("3\n")
    assert read("first") == [1, 3, 2]
    read("second")
    read("third")
    assert len(cache._entries) == 2
    assert cache._bytes == 128


@pytest.mark.parametrize("ending", ["", "\n", "\r\n", "\r"])
@pytest.mark.parametrize("limit", [1, 2, 20])
def test_reverse_tail_matches_text_splitlines(tmp_path, ending, limit):
    path = tmp_path / "log"
    text = ("å🙂 x" * 5000 + "\n") * 5 + "last line" + ending
    path.write_bytes(text.encode())
    expected = "\n".join(path.read_text().splitlines()[-limit:]) + "\n"
    assert read_log_tail(path, limit) == expected


def test_tail_merge_preserves_overlap_and_no_overlap_semantics(tmp_path):
    path = tmp_path / "log"
    rng = random.Random(72)
    pairs = [
        ("", "x"),
        ("\n\n", "x"),
        ("abc", "abcdef"),
        ("abcdef", "defghi"),
        ("a" * 10000, "b" * 10000),
    ]
    for _ in range(200):
        old = "".join(rng.choice("abcå🙂\n") for _ in range(rng.randrange(1, 100)))
        tail = old[-rng.randrange(1, len(old) + 1) :] + "".join(
            rng.choice("abc\n") for _ in range(10)
        )
        pairs.append((old, tail))
    for old, tail in pairs:
        path.write_text(old)
        if not old or tail.startswith(old):
            expected = tail
        else:
            overlap = next(
                (n for n in range(min(len(old), len(tail)), 0, -1) if old.endswith(tail[:n])), 0
            )
            expected = old + tail[overlap:] if overlap else old.rstrip("\n") + "\n" + tail
        merge_tail_log_snapshot(path, tail)
        assert path.read_text() == expected


def _event(kind, **payload):
    return {
        "timestamp": "2026-09-22T12:00:00Z",
        "type": "response_item",
        "payload": {"type": kind, **payload},
    }


def test_worker_tool_and_message_projection_updates_on_append(tmp_path, monkeypatch):
    sessions = tmp_path / ".codex" / "sessions"
    sessions.mkdir(parents=True)
    path = sessions / "rollout-test.jsonl"
    events = [_event("function_call", name="exec", call_id="call-1", arguments='{"command":"pwd"}')]
    path.write_text("".join(json.dumps(event) + "\n" for event in events))
    monkeypatch.setattr(service, "_TRANSCRIPT_PROJECTIONS", JsonlProjectionCache())
    parser_calls = []
    original = service._codex_messages_from_session_event

    def parse(event, **kwargs):
        parser_calls.append(event)
        return original(event, **kwargs)

    monkeypatch.setattr(service, "_codex_messages_from_session_event", parse)
    active = service._codex_active_tool_context(tmp_path, "test")
    assert active["tool_name"] == "exec"
    initial_calls = len(parser_calls)
    assert service._codex_active_tool_context(tmp_path, "test") == active
    assert len(parser_calls) == initial_calls
    with path.open("a") as output:
        output.write(
            json.dumps(_event("function_call_output", call_id="call-1", output="done")) + "\n"
        )
    assert service._codex_active_tool_context(tmp_path, "test") is None
    assert len(parser_calls) == initial_calls + 1
    messages = service.WorkerDaemon._codex_session_messages(tmp_path, "test")
    assert messages == ["tool started: exec pwd", "tool completed: exec pwd"]
    calls_after_messages = len(parser_calls)
    assert service.WorkerDaemon._codex_session_messages(tmp_path, "test") == messages
    assert len(parser_calls) == calls_after_messages
    assert (
        service.WorkerDaemon._codex_session_messages(
            tmp_path, "test", started_at=datetime(2027, 1, 1, tzinfo=UTC)
        )
        == []
    )


def test_tail_overlap_after_bare_cr_keeps_both_logical_newlines(tmp_path):
    path = tmp_path / "log"
    path.write_bytes(b"a\r")
    merge_tail_log_snapshot(path, "\n\nb")
    assert path.read_text() == "a\n\nb"


@pytest.mark.parametrize("trailing_crs", [1, 2, 100])
@pytest.mark.parametrize("earlier_crlf", [False, True])
def test_tail_overlap_preserves_cr_runs_and_preceding_crlf(tmp_path, trailing_crs, earlier_crlf):
    path = tmp_path / "log"
    path.write_bytes(b"xa" + (b"\r\n" if earlier_crlf else b"") + b"\r" * trailing_crs)
    expected_newlines = trailing_crs + int(earlier_crlf) + 1
    merge_tail_log_snapshot(path, "a" + "\n" * expected_newlines + "b")
    assert path.read_text() == "xa" + "\n" * expected_newlines + "b"


def test_tail_overlap_preserves_cr_run_longer_than_suffix_window(tmp_path):
    path = tmp_path / "log"
    existing = b"x" + b"\r" * 1000
    path.write_bytes(existing)
    merge_tail_log_snapshot(path, "\n\nB")
    assert path.read_text() == "x" + "\n" * 1000 + "B"
    assert path.read_bytes().startswith(existing)


def test_cold_projection_does_not_block_other_cached_transcripts(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    cache = JsonlProjectionCache()
    warm = tmp_path / "warm"
    cold = tmp_path / "cold"
    warm.write_text("1\n")
    cold.write_text("2\n")
    entered = Event()
    release = Event()

    def consume(state, value):
        if value == 2:
            entered.set()
            assert release.wait(3)
        state.append(value)

    def read(path):
        return cache.read("events", [path], factory=list, consume=consume, weight=lambda state: 64)

    assert read(warm) == [1]
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(read, cold)
        try:
            assert entered.wait(1)
            assert pool.submit(read, warm).result(timeout=1) == [1]
        finally:
            release.set()
        assert pending.result(timeout=1) == [2]
    assert cache._projection_locks == {}

from __future__ import annotations

import asyncio
import base64
import gzip
import json
from pathlib import Path

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from evander.models import ToolName
from gateway_tool_support import call_tool
from test_gateway_ado_build_tools import gateway, install_transport


TIMELINE = "d377239b-8876-42c2-a708-48ea60e6ee78"
NESTED = "83ac75be-616d-4604-bb19-87d2852d8fb7"


def test_timeline_preserves_failure_hierarchy_nested_timelines_and_paging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = gateway(tmp_path, monkeypatch, [ToolName.GET_ADO_BUILD_TIMELINE])

    def respond(request):
        assert request.method == "GET"
        assert request.url.params["api-version"] == "7.1"
        assert request.headers["authorization"] == "Basic OnRlYW0tc2VjcmV0LXRva2Vu"
        if request.url.path.endswith(NESTED):
            return httpx.Response(200, json={"id": NESTED, "records": []})
        assert request.url.path == "/example/TeamProject/_apis/build/builds/42/timeline"
        return httpx.Response(200, json={"id": TIMELINE, "changeId": 3, "records": [
            {"id": "stage", "name": "Verify", "type": "Stage", "state": "completed", "result": "failed"},
            {"id": "job", "parentId": "stage", "name": "Unit tests", "type": "Job", "result": "failed"},
            {"id": "task", "parentId": "job", "name": "Run tests", "type": "Task",
             "state": "completed", "result": "failed", "attempt": 2, "errorCount": 1,
             "issues": [{"type": "error", "category": "Test", "message": "Expected status 200; got 500"}],
             "log": {"id": 7, "url": "https://untrusted.example/never-fetch"},
             "details": {"id": NESTED},
             "previousAttempts": [{"attempt": 1, "timelineId": NESTED, "recordId": "previous-task"}]},
        ]})

    requests = install_transport(monkeypatch, respond)
    first = call_tool(server, "get_ado_build_timeline", {"session_id": "session-1", "build_id": 42, "limit": 2})
    last = call_tool(server, "get_ado_build_timeline", {
        "session_id": "session-1", "build_id": 42, "skip": first["next_skip"], "limit": 2,
    })
    nested = call_tool(server, "get_ado_build_timeline", {
        "session_id": "session-1", "build_id": 42, "timeline_id": last["records"][0]["details_timeline_id"],
    })

    assert first["project"] == "TeamProject"
    assert [r["id"] for r in first["records"]] == ["stage", "job"]
    assert first["records"][1]["parent_id"] == "stage"
    assert first["total_count"] == 3
    assert first["truncated"] is True
    assert last["count"] == 1 and last["next_skip"] is None
    task = last["records"][0]
    assert (task["parent_id"], task["result"], task["attempt"], task["error_count"], task["log_id"]) == (
        "job", "failed", 2, 1, "7",
    )
    assert task["issues"] == [{"type": "error", "category": "Test", "message": "Expected status 200; got 500"}]
    assert task["previous_attempts"] == [{"attempt": 1, "timeline_id": NESTED, "record_id": "previous-task"}]
    assert nested["records"] == [] and nested["timeline_id"] == NESTED
    assert len(requests) == 3
    assert "untrusted.example" not in json.dumps(last)
    assert "Expected status" not in (tmp_path / "audit.jsonl").read_text()


def test_log_defaults_to_tail_and_supports_forward_windows_with_safe_auditing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = gateway(tmp_path, monkeypatch, [ToolName.GET_ADO_BUILD_LOG])
    # This independent, numbered log makes off-by-one offsets observable.
    lines = [f"diagnostic line {i}\n" for i in range(250)]

    def respond(request):
        assert request.method == "GET"
        assert request.url.path.startswith("/example/TeamProject/_apis/build/builds/42/logs")
        assert request.headers["authorization"] == "Basic OnRlYW0tc2VjcmV0LXRva2Vu"
        if request.url.path.endswith("/logs"):
            return httpx.Response(200, json={"value": [{"id": 7, "lineCount": 250, "url": "https://untrusted.example"}]})
        assert request.headers["accept"] == "text/plain"
        start, end = int(request.url.params["startLine"]), int(request.url.params["endLine"])
        # ADO's endpoint is one-based and inclusive. The MCP interface is
        # deliberately zero-based with an exclusive end.
        return httpx.Response(200, text="".join(lines[start - 1:end]))

    requests = install_transport(monkeypatch, respond)
    args = {"session_id": "session-1", "build_id": 42, "log_id": 7}
    tail = call_tool(server, "get_ado_build_log", args)
    forward = call_tool(server, "get_ado_build_log", {**args, "start_line": 0, "max_lines": 3})

    assert (tail["start_line"], tail["end_line"], tail["returned_lines"], tail["total_lines"]) == (50, 250, 200, 250)
    assert tail["text"].startswith("diagnostic line 50\n")
    assert tail["text"].endswith("diagnostic line 249\n")
    assert tail["next_start_line"] is None and tail["truncated"] is True
    assert forward["text"] == "diagnostic line 0\ndiagnostic line 1\ndiagnostic line 2\n"
    assert forward["next_start_line"] == 3
    assert len(requests) == 4
    assert (requests[1].url.params["startLine"], requests[1].url.params["endLine"]) == ("51", "250")
    assert (requests[3].url.params["startLine"], requests[3].url.params["endLine"]) == ("1", "3")
    audit = (tmp_path / "audit.jsonl").read_text()
    assert "diagnostic line" not in audit and "team-secret-token" not in audit
    assert '"returned_lines": 200' in audit


@pytest.mark.parametrize("tool,arguments", [
    ("get_ado_build_timeline", {"build_id": 42}),
    ("get_ado_build_log", {"build_id": 42, "log_id": 7}),
])
def test_diagnostic_tools_need_their_own_grants_before_network_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tool: str, arguments: dict,
) -> None:
    other = ToolName.GET_ADO_BUILD_LOG if tool.endswith("timeline") else ToolName.GET_ADO_BUILD_TIMELINE
    server = gateway(tmp_path, monkeypatch, [ToolName.GET_ADO_BUILD, other])
    requests = install_transport(monkeypatch, lambda _: pytest.fail("unexpected ADO request"))
    with pytest.raises(ToolError, match="not allowed"):
        asyncio.run(server.call_tool(tool, {"session_id": "session-1", **arguments}))
    assert requests == []


@pytest.mark.parametrize("tool,arguments", [
    ("get_ado_build_timeline", {"build_id": 0}),
    ("get_ado_build_timeline", {"build_id": True}),
    ("get_ado_build_timeline", {"build_id": 42, "timeline_id": "../other-project"}),
    ("get_ado_build_timeline", {"build_id": 42, "skip": -1}),
    ("get_ado_build_timeline", {"build_id": 42, "limit": 1001}),
    ("get_ado_build_timeline", {"build_id": 42, "limit": True}),
    ("get_ado_build_log", {"build_id": 42, "log_id": "bad"}),
    ("get_ado_build_log", {"build_id": 42, "log_id": 7, "start_line": -1}),
    ("get_ado_build_log", {"build_id": 42, "log_id": 7, "max_lines": 0}),
    ("get_ado_build_log", {"build_id": 42, "log_id": 7, "max_lines": 1001}),
    ("get_ado_build_log", {"build_id": 42, "log_id": 7, "max_lines": 1.5}),
])
def test_diagnostic_validation_rejects_invalid_paths_and_ranges_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tool: str, arguments: dict,
) -> None:
    server = gateway(tmp_path, monkeypatch, [ToolName.GET_ADO_BUILD_TIMELINE, ToolName.GET_ADO_BUILD_LOG])
    requests = install_transport(monkeypatch, lambda _: pytest.fail("unexpected ADO request"))
    with pytest.raises(ToolError):
        asyncio.run(server.call_tool(tool, {"session_id": "session-1", **arguments}))
    assert requests == []


def test_compressed_diagnostic_responses_are_decoded_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = gateway(tmp_path, monkeypatch, [ToolName.GET_ADO_BUILD_TIMELINE, ToolName.GET_ADO_BUILD_LOG])

    def respond(request):
        if request.url.path.endswith("/timeline"):
            content = json.dumps({"id": TIMELINE, "records": []}).encode()
        elif request.url.path.endswith("/logs"):
            content = b'{"value":[{"id":7,"lineCount":2}]}'
        else:
            content = "test failed: förväntat 200\nactual: 500\n".encode()
        return httpx.Response(200, content=gzip.compress(content), headers={"content-encoding": "gzip"})

    install_transport(monkeypatch, respond)
    timeline = call_tool(server, "get_ado_build_timeline", {"session_id": "session-1", "build_id": 42})
    log = call_tool(server, "get_ado_build_log", {"session_id": "session-1", "build_id": 42, "log_id": 7})
    assert timeline["records"] == []
    assert log["text"] == "test failed: förväntat 200\nactual: 500\n"
    assert log["returned_lines"] == 2 and log["truncated"] is False


class CountingStream(httpx.SyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.read_count = 0
        self.closed = False

    def __iter__(self):
        for chunk in self.chunks:
            self.read_count += 1
            yield chunk

    def close(self):
        self.closed = True


def test_log_paging_preserves_carriage_returns_unicode_separators_and_crlf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = gateway(tmp_path, monkeypatch, [ToolName.GET_ADO_BUILD_LOG])
    lines = ["progress 10%\rbuild failed\n", "expected first\u2028second; got empty\n", "last diagnostic\r\n"]

    def respond(request):
        if request.url.path.endswith("/logs"):
            return httpx.Response(200, json={"value": [{"id": 7, "lineCount": 3}]})
        start, end = int(request.url.params["startLine"]), int(request.url.params["endLine"])
        return httpx.Response(200, text="".join(lines[start - 1:end]))

    install_transport(monkeypatch, respond)
    args = {"session_id": "session-1", "build_id": 42, "log_id": 7, "max_lines": 2}
    first = call_tool(server, "get_ado_build_log", {**args, "start_line": 0})
    second = call_tool(server, "get_ado_build_log", {**args, "start_line": first["next_start_line"]})
    assert first["text"] == "progress 10%\rbuild failed\nexpected first\u2028second; got empty\n"
    assert first["returned_lines"] == 2 and first["next_start_line"] == 2
    assert second["text"] == "last diagnostic\r\n"
    assert second["returned_lines"] == 1 and second["end_line"] == 3
    assert second["next_start_line"] is None


def test_oversized_log_stops_reading_and_marks_partial_utf8_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = gateway(tmp_path, monkeypatch, [ToolName.GET_ADO_BUILD_LOG])
    stream = CountingStream([("ö" * 4096).encode()] * 1000)

    def respond(request):
        if request.url.path.endswith("/logs"):
            return httpx.Response(200, json={"value": [{"id": 7, "lineCount": 1}]})
        return httpx.Response(200, stream=stream)

    install_transport(monkeypatch, respond)
    log = call_tool(server, "get_ado_build_log", {"session_id": "session-1", "build_id": 42, "log_id": 7})
    assert len(log["text"].encode()) == 65536
    assert log["text"] == "ö" * 32768
    assert log["byte_truncated"] is True and log["partial_last_line"] is True
    assert log["end_line"] == 1 and log["next_start_line"] is None
    assert stream.closed and stream.read_count < 12


@pytest.mark.parametrize("encoded", [False, True])
def test_reflected_authentication_is_rejected_even_across_log_byte_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, encoded: bool,
) -> None:
    server = gateway(tmp_path, monkeypatch, [ToolName.GET_ADO_BUILD_LOG])
    token = base64.b64encode(b":team-secret-token").decode() if encoded else "team-secret-token"

    def respond(request):
        if request.url.path.endswith("/logs"):
            return httpx.Response(200, json={"value": [{"id": 7, "lineCount": 1}]})
        return httpx.Response(200, text="x" * (65536 - 4) + token)

    install_transport(monkeypatch, respond)
    with pytest.raises(ToolError, match="host authentication material") as error:
        asyncio.run(server.call_tool("get_ado_build_log", {"session_id": "session-1", "build_id": 42, "log_id": 7}))
    audit = (tmp_path / "audit.jsonl").read_text()
    assert token not in str(error.value) and token not in audit


def test_byte_cap_ending_at_embedded_carriage_return_marks_partial_physical_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = gateway(tmp_path, monkeypatch, [ToolName.GET_ADO_BUILD_LOG])

    def respond(request):
        if request.url.path.endswith("/logs"):
            return httpx.Response(200, json={"value": [{"id": 7, "lineCount": 1}]} )
        return httpx.Response(200, content=b"x" * 65535 + b"\rdiagnostic failure\n")

    install_transport(monkeypatch, respond)
    log = call_tool(server, "get_ado_build_log", {"session_id": "session-1", "build_id": 42, "log_id": 7})
    assert log["text"].endswith("\r") and len(log["text"].encode()) == 65536
    assert log["partial_last_line"] is True and log["byte_truncated"] is True
    assert log["returned_lines"] == 1 and log["end_line"] == 1


def test_timeline_reflected_credentials_are_rejected_before_return_and_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = gateway(tmp_path, monkeypatch, [ToolName.GET_ADO_BUILD_TIMELINE])
    install_transport(monkeypatch, lambda _: httpx.Response(200, json={
        "id": TIMELINE, "records": [{"id": "task", "issues": [{"message": "team-secret-token"}]}],
    }))
    with pytest.raises(ToolError, match="host authentication material"):
        asyncio.run(server.call_tool("get_ado_build_timeline", {"session_id": "session-1", "build_id": 42}))
    assert "team-secret-token" not in (tmp_path / "audit.jsonl").read_text()


@pytest.mark.parametrize("response,message", [
    (httpx.Response(404, text="private unavailable log details"), "HTTP 404"),
    (httpx.Response(403, text="private access details"), "HTTP 403"),
    (httpx.Response(302, headers={"location": "https://untrusted.example/private"}), "HTTP 302"),
    (httpx.Response(200, text="private invalid JSON"), "invalid build diagnostic"),
    (httpx.Response(200, json={"id": TIMELINE, "records": "private bad timeline"}), "invalid build timeline"),
])
def test_provider_errors_are_sanitized_and_redirects_not_followed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, response: httpx.Response, message: str,
) -> None:
    server = gateway(tmp_path, monkeypatch, [ToolName.GET_ADO_BUILD_TIMELINE])
    requests = install_transport(monkeypatch, lambda _: response)
    with pytest.raises(ToolError, match=message) as error:
        asyncio.run(server.call_tool("get_ado_build_timeline", {"session_id": "session-1", "build_id": 42}))
    assert "private" not in str(error.value)
    assert "untrusted.example" not in (tmp_path / "audit.jsonl").read_text()
    assert len(requests) == 1


def test_unavailable_and_empty_logs_do_not_attempt_a_log_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = gateway(tmp_path, monkeypatch, [ToolName.GET_ADO_BUILD_LOG])
    requests = install_transport(monkeypatch, lambda _: httpx.Response(200, json={"value": [{"id": 7, "lineCount": 0}]}))
    empty = call_tool(server, "get_ado_build_log", {"session_id": "session-1", "build_id": 42, "log_id": 7})
    assert empty["text"] == "" and empty["total_lines"] == 0 and empty["next_start_line"] is None
    with pytest.raises(ToolError, match="not available for this build"):
        asyncio.run(server.call_tool("get_ado_build_log", {"session_id": "session-1", "build_id": 42, "log_id": 8}))
    assert len(requests) == 2 and all(r.url.path.endswith("/logs") for r in requests)


def test_oversized_timeline_is_rejected_without_reading_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = gateway(tmp_path, monkeypatch, [ToolName.GET_ADO_BUILD_TIMELINE])
    stream = CountingStream([b"x" * 65536] * 1000)
    install_transport(monkeypatch, lambda _: httpx.Response(200, stream=stream))
    with pytest.raises(ToolError, match="exceeded its byte limit"):
        asyncio.run(server.call_tool("get_ado_build_timeline", {"session_id": "session-1", "build_id": 42}))
    assert stream.closed and stream.read_count < 35


def test_diagnostic_schemas_advertise_permissions_defaults_and_bounds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = gateway(tmp_path, monkeypatch, [ToolName.GET_ADO_BUILD_TIMELINE, ToolName.GET_ADO_BUILD_LOG])
    tools = {tool.name: tool.model_dump(by_alias=True) for tool in asyncio.run(server.list_tools())}
    for name in ("get_ado_build_timeline", "get_ado_build_log"):
        assert tools[name]["annotations"]["readOnlyHint"] is True
    timeline = tools["get_ado_build_timeline"]["inputSchema"]["properties"]
    log = tools["get_ado_build_log"]["inputSchema"]["properties"]
    assert timeline["skip"]["default"] == 0 and timeline["skip"]["minimum"] == 0
    assert timeline["limit"]["default"] == 200 and timeline["limit"]["maximum"] == 1000
    assert log["start_line"]["default"] is None
    assert log["max_lines"]["default"] == 200 and log["max_lines"]["maximum"] == 1000

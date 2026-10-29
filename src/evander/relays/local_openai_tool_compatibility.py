from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class LocalOpenAIToolCompatibility:
    """Request-scoped state for adapting namespace tools to local OpenAI APIs."""

    request_body: bytes
    function_namespaces: dict[str, tuple[str, str]]
    top_level_function_names: frozenset[str]


class LocalOpenAIToolCompatibilityAdapter:
    """Translate namespaced Responses API tools for OpenAI-compatible local models."""

    def prepare_request(
        self,
        *,
        method: str,
        upstream_path: str,
        request_body: bytes,
    ) -> LocalOpenAIToolCompatibility:
        unchanged = LocalOpenAIToolCompatibility(
            request_body=request_body,
            function_namespaces={},
            top_level_function_names=frozenset(),
        )
        if method != "POST" or upstream_path != "responses":
            return unchanged
        try:
            payload = json.loads(request_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return unchanged
        if not isinstance(payload, dict) or not isinstance(payload.get("tools"), list):
            return unchanged

        rewritten_tools: list[object] = []
        function_namespaces: dict[str, tuple[str, str]] = {}
        top_level_function_names = frozenset(
            str(tool.get("name") or "").strip()
            for tool in payload["tools"]
            if isinstance(tool, dict)
            and tool.get("type") == "function"
            and str(tool.get("name") or "").strip()
        )
        changed = False
        for tool in payload["tools"]:
            if not isinstance(tool, dict) or tool.get("type") != "namespace":
                rewritten_tools.append(tool)
                continue
            namespace = str(tool.get("name") or "").strip()
            namespace_tools = tool.get("tools")
            if not namespace or not isinstance(namespace_tools, list):
                rewritten_tools.append(tool)
                continue
            flattened_namespace_tools: list[dict[str, object]] = []
            for namespace_tool in namespace_tools:
                if not isinstance(namespace_tool, dict):
                    continue
                function_name = str(namespace_tool.get("name") or "").strip()
                if not function_name:
                    continue
                flattened_name = self._flattened_tool_name(
                    namespace=namespace,
                    function_name=function_name,
                )
                if flattened_name in function_namespaces:
                    continue
                flattened_tool = dict(namespace_tool)
                flattened_tool["type"] = "function"
                flattened_tool["name"] = flattened_name
                flattened_namespace_tools.append(flattened_tool)
                function_namespaces[flattened_name] = (namespace, function_name)
            if not flattened_namespace_tools:
                rewritten_tools.append(tool)
                continue
            rewritten_tools.extend(flattened_namespace_tools)
            changed = True

        if not changed:
            return unchanged
        payload["tools"] = rewritten_tools
        return LocalOpenAIToolCompatibility(
            request_body=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            function_namespaces=function_namespaces,
            top_level_function_names=top_level_function_names,
        )

    def rewrite_response(
        self,
        response_body: bytes,
        *,
        compatibility: LocalOpenAIToolCompatibility,
    ) -> bytes:
        if not compatibility.function_namespaces:
            return response_body
        try:
            payload = json.loads(response_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return response_body
        if not self._rewrite_function_calls_payload(payload, compatibility=compatibility):
            return response_body
        return json.dumps(payload, separators=(",", ":")).encode("utf-8")

    def rewrite_stream_chunk(
        self,
        *,
        buffer: bytes,
        chunk: bytes,
        compatibility: LocalOpenAIToolCompatibility,
    ) -> tuple[bytes, bytes]:
        if not compatibility.function_namespaces:
            return buffer + chunk, b""
        pending = buffer + chunk
        complete_lines = pending.splitlines(keepends=True)
        if complete_lines and not complete_lines[-1].endswith((b"\n", b"\r")):
            remainder = complete_lines.pop()
        else:
            remainder = b""
        rewritten = b"".join(
            self.rewrite_sse_line(line, compatibility=compatibility) for line in complete_lines
        )
        return rewritten, remainder

    def rewrite_sse_line(
        self,
        line: bytes,
        *,
        compatibility: LocalOpenAIToolCompatibility,
    ) -> bytes:
        if not compatibility.function_namespaces or not line.startswith(b"data:"):
            return line
        body_and_ending = line[len(b"data:") :]
        without_ending = body_and_ending.rstrip(b"\r\n")
        body = without_ending.strip()
        ending = body_and_ending[len(without_ending) :]
        if not body or body == b"[DONE]":
            return line
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return line
        if not self._rewrite_function_calls_payload(payload, compatibility=compatibility):
            return line
        rewritten = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return b"data: " + rewritten + ending

    @staticmethod
    def _flattened_tool_name(*, namespace: str, function_name: str) -> str:
        separator = "" if namespace.endswith("__") else "__"
        return f"{namespace}{separator}{function_name}"

    def _rewrite_function_calls_payload(
        self,
        payload: object,
        *,
        compatibility: LocalOpenAIToolCompatibility,
    ) -> bool:
        changed = False
        if isinstance(payload, list):
            for item in payload:
                changed = (
                    self._rewrite_function_calls_payload(item, compatibility=compatibility)
                    or changed
                )
            return changed
        if not isinstance(payload, dict):
            return False
        if payload.get("type") == "function_call" and not payload.get("namespace"):
            function_name = str(payload.get("name") or "").strip()
            resolved = self._resolve_function_namespace(
                function_name,
                compatibility=compatibility,
            )
            if resolved is not None:
                namespace, inner_name = resolved
                payload["namespace"] = namespace
                payload["name"] = inner_name
                changed = True
        for value in payload.values():
            changed = (
                self._rewrite_function_calls_payload(value, compatibility=compatibility) or changed
            )
        return changed

    @staticmethod
    def _resolve_function_namespace(
        function_name: str,
        *,
        compatibility: LocalOpenAIToolCompatibility,
    ) -> tuple[str, str] | None:
        if function_name in compatibility.top_level_function_names:
            return None
        candidates = [function_name]
        separator_normalized = function_name
        for separator in (":", ".", "/"):
            separator_normalized = separator_normalized.replace(separator, "__")
        if separator_normalized not in candidates:
            candidates.append(separator_normalized)
        for candidate in tuple(candidates):
            if candidate.startswith("mcp__"):
                candidates.append(candidate[len("mcp__") :])
            else:
                candidates.append(f"mcp__{candidate}")
        for candidate in candidates:
            resolved = compatibility.function_namespaces.get(candidate)
            if resolved is not None:
                return resolved
        inner_name_candidates = {
            candidate[len("mcp__") :] if candidate.startswith("mcp__") else candidate
            for candidate in candidates
        }
        inner_name_matches = {
            resolved
            for resolved in compatibility.function_namespaces.values()
            if resolved[1] in inner_name_candidates
        }
        if len(inner_name_matches) == 1:
            return next(iter(inner_name_matches))
        return None

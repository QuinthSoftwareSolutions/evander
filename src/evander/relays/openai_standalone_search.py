from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit


SUPPORTED_COMMANDS = frozenset(
    {"search_query", "image_query", "open", "find", "finance", "weather", "sports", "time"}
)
UNSUPPORTED_COMMANDS = frozenset({"click", "screenshot"})


def prepare_standalone_search_request(payload: Any, *, session_model: str | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Standalone web search request must be a JSON object")

    requested_model = payload.get("model")
    if not isinstance(requested_model, str) or not requested_model.strip():
        raise ValueError("Standalone web search requires a model")
    if session_model and requested_model != session_model:
        raise ValueError("Standalone web search model does not match the session model")

    commands = payload.get("commands")
    if not isinstance(commands, dict):
        raise ValueError("Standalone web search requires commands")
    unsupported = sorted(key for key in UNSUPPORTED_COMMANDS if commands.get(key))
    if unsupported:
        raise ValueError(f"Standalone web search does not support: {', '.join(unsupported)}")
    unknown = sorted(
        key
        for key in commands
        if key not in SUPPORTED_COMMANDS | UNSUPPORTED_COMMANDS | {"response_length"}
    )
    if unknown:
        raise ValueError(f"Unknown standalone web search commands: {', '.join(unknown)}")

    instructions: list[str] = []
    query_domains: list[list[str]] = []
    image_search = False
    for command, operations in commands.items():
        if command == "response_length":
            continue
        if not isinstance(operations, list):
            raise ValueError(f"Standalone web search {command} must be a list")
        for operation in operations:
            if not isinstance(operation, dict):
                raise ValueError(f"Standalone web search {command} entries must be objects")
            if command in {"search_query", "image_query"}:
                query = _required_text(operation, "q", command)
                details = [f"Search for {query!r}."]
                if "recency" in operation:
                    days = operation["recency"]
                    if not isinstance(days, int) or isinstance(days, bool) or days < 0:
                        raise ValueError(
                            f"Standalone web search {command}.recency must be a nonnegative integer"
                        )
                    details.append(f"Prioritize results from the last {days} days.")
                domains = operation.get("domains")
                if domains is not None:
                    query_domains.append(_domain_list(domains, f"{command}.domains"))
                instructions.append(" ".join(details))
                image_search = image_search or command == "image_query"
            elif command == "open":
                url = _required_url(operation, "ref_id", command)
                instructions.append(f"Open and summarize {url}.")
            elif command == "find":
                url = _required_url(operation, "ref_id", command)
                pattern = _required_text(operation, "pattern", command)
                instructions.append(
                    f"Find {pattern!r} on {url} and quote the relevant context briefly."
                )
            elif command == "finance":
                ticker = _required_text(operation, "ticker", command)
                asset_type = _required_text(operation, "type", command)
                market = operation.get("market")
                instructions.append(
                    f"Find the current {asset_type} price for {ticker} in market {market or 'default'} and state its timestamp."
                )
            elif command == "weather":
                location = _required_text(operation, "location", command)
                instructions.append(
                    f"Find the weather forecast for {location}, starting {operation.get('start') or 'today'} for {operation.get('duration') or 7} days."
                )
            elif command == "sports":
                league = _required_text(operation, "league", command)
                function = _required_text(operation, "fn", command)
                instructions.append(
                    f"Find {league} {function} information for {operation.get('team') or 'the league'} with the requested dates: {operation.get('date_from') or 'current'} to {operation.get('date_to') or 'current'}."
                )
            elif command == "time":
                offset = _required_text(operation, "utc_offset", command)
                instructions.append(f"Find the current time at UTC offset {offset}.")

    if not instructions:
        raise ValueError("Standalone web search requires at least one supported command")

    settings = payload.get("settings") or {}
    if not isinstance(settings, dict):
        raise ValueError("Standalone web search settings must be an object")
    tool: dict[str, Any] = {"type": "web_search"}
    filters = settings.get("filters") or {}
    if not isinstance(filters, dict):
        raise ValueError("Standalone web search filters must be an object")
    upstream_filters: dict[str, Any] = {}
    for key in ("allowed_domains", "blocked_domains"):
        if key in filters:
            upstream_filters[key] = _domain_list(filters[key], f"settings.filters.{key}")
    if query_domains:
        if len(query_domains) != 1 or "allowed_domains" in upstream_filters:
            raise ValueError(
                "Per-query domain filters require one query and no global allowed_domains filter"
            )
        upstream_filters["allowed_domains"] = query_domains[0]
    if upstream_filters:
        tool["filters"] = upstream_filters

    context_size = settings.get("search_context_size")
    if context_size is not None:
        if context_size not in {"low", "medium", "high"}:
            raise ValueError("Invalid standalone web search context size")
        tool["search_context_size"] = context_size
    access = settings.get("external_web_access")
    if access is not None:
        if access not in (True, False, "live", "cached", "indexed"):
            raise ValueError("Invalid standalone web search external_web_access")
        tool["external_web_access"] = access is True or access == "live"
    if image_search:
        tool["search_content_types"] = ["image", "text"]
        image_settings = settings.get("image_settings")
        if image_settings is not None:
            if not isinstance(image_settings, dict):
                raise ValueError("Standalone web search image_settings must be an object")
            tool["image_settings"] = {
                key: image_settings[key]
                for key in ("max_results", "caption")
                if key in image_settings
            }

    length = commands.get("response_length", "medium")
    if length not in {"short", "medium", "long"}:
        raise ValueError("Invalid standalone web search response_length")
    max_tokens = payload.get("max_output_tokens")
    if max_tokens is not None and (
        not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens < 1
    ):
        raise ValueError("Standalone web search max_output_tokens must be a positive integer")

    return {
        "model": session_model or requested_model,
        "input": (
            "Carry out these web operations. Use the web search tool. Give factual results "
            f"with direct source URLs. Keep the answer {length}.\n"
            + "\n".join(f"{index}. {item}" for index, item in enumerate(instructions, 1))
        ),
        "tools": [tool],
        "tool_choice": "required",
        "include": ["web_search_call.action.sources", "web_search_call.results"],
        "max_output_tokens": min(max_tokens or 4000, 8000),
    }


def standalone_search_response(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("output"), list):
        raise ValueError("Upstream web search response has no output items")
    if payload.get("status", "completed") != "completed":
        raise ValueError("Upstream web search response is incomplete")

    passages: list[str] = []
    sources: list[tuple[str, str]] = []
    has_search_call = False
    for item in payload["output"]:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "web_search_call":
            has_search_call = has_search_call or item.get("status", "completed") == "completed"
            action = item.get("action") or {}
            if isinstance(action, dict):
                for source in action.get("sources") or []:
                    _append_source(sources, source)
            for result in item.get("results") or []:
                _append_source(sources, result)
        if item.get("type") == "message":
            for content in item.get("content") or []:
                if not isinstance(content, dict):
                    continue
                if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                    passages.append(content["text"])
                for annotation in content.get("annotations") or []:
                    _append_source(sources, annotation)

    if not has_search_call or not passages:
        raise ValueError("Upstream response did not complete a web search")
    output = "\n\n".join(passages)
    if sources:
        output += "\n\nSources:\n" + "\n".join(f"- {title}: {url}" for title, url in sources)
    return {"encrypted_output": None, "output": output}


def _required_text(operation: dict[str, Any], key: str, command: str) -> str:
    value = operation.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Standalone web search {command}.{key} must be a nonempty string")
    return value.strip()


def _required_url(operation: dict[str, Any], key: str, command: str) -> str:
    value = _required_text(operation, key, command)
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"Standalone web search {command}.{key} must be an HTTP URL")
    return value


def _domain_list(value: Any, field: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(domain, str) and domain.strip() for domain in value)
    ):
        raise ValueError(f"Standalone web search {field} must be a nonempty domain list")
    return value


def _append_source(sources: list[tuple[str, str]], value: Any) -> None:
    if not isinstance(value, dict):
        return
    url = value.get("url") or value.get("source_url")
    if not isinstance(url, str) or urlsplit(url).scheme not in {"http", "https"}:
        return
    title = value.get("title") or value.get("name") or "Source"
    source = (str(title), url)
    if source not in sources:
        sources.append(source)

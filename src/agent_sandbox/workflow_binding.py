from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

_OUTPUT_REF_EXACT_RE = re.compile(
    r"^\$\{\{([A-Za-z0-9][A-Za-z0-9_-]*)\.([A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*)\}\}$"
)
_OUTPUT_REF_ANY_RE = re.compile(
    r"\$\{\{([A-Za-z0-9][A-Za-z0-9_-]*)\.([A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*)\}\}"
)
_OUTPUT_REF_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*\.[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*$")


class BindingResolutionError(ValueError):
    """Raised when config binding substitution fails validation."""


@dataclass(frozen=True)
class BindingResolutionTrace:
    consumed_output_keys: tuple[str, ...]


@dataclass(frozen=True)
class BindingResolutionResult:
    resolved_config: Any
    trace: BindingResolutionTrace


def resolve_workflow_bindings(
    raw_config: Any,
    upstream_payload: dict[str, dict[str, Any]],
    declared_upstream_output_names: dict[str, set[str] | list[str] | tuple[str, ...]],
    *,
    system_value_resolver: Callable[[str], Any] | None = None,
    strict_validation: bool = True,
) -> BindingResolutionResult:
    """Resolve `${{node_id.key}}` and nested `${{node_id.key.path}}` bindings against upstream payload values."""

    declared_names = {node_id: set(output_names) for node_id, output_names in declared_upstream_output_names.items()}
    consumed: set[str] = set()

    def _resolve_value(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: _resolve_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [_resolve_value(item) for item in value]
        if isinstance(value, str):
            return _resolve_string(value)
        return value

    def _resolve_string(text: str) -> Any:
        exact_match = _OUTPUT_REF_EXACT_RE.fullmatch(text)
        if exact_match:
            node_id, output_path = exact_match.groups()
            return _consume_output(node_id, output_path)

        _validate_malformed_patterns(text)

        def _replace(match: re.Match[str]) -> str:
            node_id, output_path = match.groups()
            return str(_consume_output(node_id, output_path))

        return _OUTPUT_REF_ANY_RE.sub(_replace, text)

    def _validate_malformed_patterns(text: str) -> None:
        if "${{" not in text:
            return
        index = 0
        while True:
            start = text.find("${{", index)
            if start == -1:
                return
            end = text.find("}}", start + 3)
            if end == -1:
                if strict_validation:
                    raise BindingResolutionError(f"Malformed output reference in string: {text!r}")
                return
            name = text[start + 3 : end]
            if not _OUTPUT_REF_NAME_RE.fullmatch(name):
                if strict_validation:
                    raise BindingResolutionError(f"Malformed output reference in string: {text!r}")
                index = end + 2
                continue
            index = end + 2

    def _consume_output(node_id: str, output_path: str) -> Any:
        output_ref = f"{node_id}.{output_path}"
        output_segments = tuple(segment for segment in output_path.split(".") if segment)
        if not output_segments:
            raise BindingResolutionError(f"Malformed output reference in string: '${{{{{output_ref}}}}}'")
        if node_id == "system":
            if system_value_resolver is None:
                if strict_validation:
                    raise BindingResolutionError(f"Output reference node {node_id!r} is not available in this upstream path")
                return f"${{{{{output_ref}}}}}"
            try:
                resolved_value = system_value_resolver(output_path)
            except Exception as exc:  # noqa: BLE001
                if strict_validation:
                    raise BindingResolutionError(str(exc)) from exc
                return f"${{{{{output_ref}}}}}"
            consumed.add(output_ref)
            return resolved_value
        output_name = output_segments[0]
        if strict_validation and node_id not in declared_names:
            raise BindingResolutionError(f"Output reference node {node_id!r} is not available in this upstream path")
        if strict_validation and output_name not in declared_names.get(node_id, set()):
            raise BindingResolutionError(f"Output reference {output_ref!r} is not declared by upstream node")
        node_payload = upstream_payload.get(node_id, {})
        try:
            resolved_value = _resolve_output_path(node_payload, output_segments)
        except BindingResolutionError:
            if strict_validation:
                raise
            return f"${{{{{output_ref}}}}}"
        consumed.add(output_ref)
        return resolved_value

    resolved = _resolve_value(raw_config)
    trace = BindingResolutionTrace(consumed_output_keys=tuple(sorted(consumed)))
    return BindingResolutionResult(resolved_config=resolved, trace=trace)


resolve_effect_config_bindings = resolve_workflow_bindings
resolve_gate_map_bindings = resolve_workflow_bindings


def _resolve_output_path(node_payload: dict[str, Any], output_segments: tuple[str, ...]) -> Any:
    current: Any = node_payload
    traversed: list[str] = []
    for segment in output_segments:
        traversed.append(segment)
        if isinstance(current, dict):
            if segment not in current:
                dotted = ".".join(traversed)
                raise BindingResolutionError(f"Output reference {dotted!r} was not found in upstream payload")
            current = current[segment]
            continue
        if isinstance(current, list):
            if not segment.isdigit():
                dotted = ".".join(traversed)
                raise BindingResolutionError(f"Output reference {dotted!r} was not found in upstream payload")
            index = int(segment)
            if index < 0 or index >= len(current):
                dotted = ".".join(traversed)
                raise BindingResolutionError(f"Output reference {dotted!r} was not found in upstream payload")
            current = current[index]
            continue
        dotted = ".".join(traversed)
        raise BindingResolutionError(f"Output reference {dotted!r} was not found in upstream payload")
    return current

__all__ = [
    "BindingResolutionError",
    "BindingResolutionTrace",
    "BindingResolutionResult",
    "resolve_workflow_bindings",
    "resolve_effect_config_bindings",
    "resolve_gate_map_bindings",
]

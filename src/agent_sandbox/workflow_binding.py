from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

_OUTPUT_REF_EXACT_RE = re.compile(r"^\$\{\{([^{}]+)\}\}$")
_OUTPUT_REF_ANY_RE = re.compile(r"\$\{\{([^{}]+)\}\}")
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


@dataclass(frozen=True)
class _BindingReference:
    node_id: str
    output_path: str


@dataclass(frozen=True)
class _BindingExpression:
    primary: _BindingReference
    fallback: _BindingReference | Any | None = None
    has_fallback: bool = False


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
            expression = _parse_binding_expression(exact_match.group(1), source_text=text)
            return _consume_expression(expression)

        _validate_malformed_patterns(text)

        def _replace(match: re.Match[str]) -> str:
            expression = _parse_binding_expression(match.group(1), source_text=match.group(0))
            return str(_consume_expression(expression))

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
            expression_text = text[start + 3 : end]
            try:
                _parse_binding_expression(expression_text, source_text=text)
            except BindingResolutionError:
                if strict_validation:
                    raise BindingResolutionError(f"Malformed output reference in string: {text!r}")
                index = end + 2
                continue
            index = end + 2

    def _consume_expression(expression: _BindingExpression) -> Any:
        try:
            return _consume_reference(expression.primary)
        except BindingResolutionError:
            if not expression.has_fallback:
                if strict_validation:
                    raise
                return _render_expression_placeholder(expression)
            fallback = expression.fallback
            if isinstance(fallback, _BindingReference):
                return _consume_reference(fallback)
            return fallback

    def _consume_reference(reference: _BindingReference) -> Any:
        output_ref = f"{reference.node_id}.{reference.output_path}"
        output_segments = tuple(segment for segment in reference.output_path.split(".") if segment)
        if not output_segments:
            raise BindingResolutionError(f"Malformed output reference in string: '${{{{{output_ref}}}}}'")
        if reference.node_id == "system":
            if system_value_resolver is None:
                if strict_validation:
                    raise BindingResolutionError(
                        f"Output reference node {reference.node_id!r} is not available in this upstream path"
                    )
                return f"${{{{{output_ref}}}}}"
            try:
                resolved_value = system_value_resolver(reference.output_path)
            except Exception as exc:  # noqa: BLE001
                if strict_validation:
                    raise BindingResolutionError(str(exc)) from exc
                return f"${{{{{output_ref}}}}}"
            consumed.add(output_ref)
            return resolved_value
        output_name = output_segments[0]
        if strict_validation and reference.node_id not in declared_names:
            raise BindingResolutionError(f"Output reference node {reference.node_id!r} is not available in this upstream path")
        if strict_validation and output_name not in declared_names.get(reference.node_id, set()):
            raise BindingResolutionError(f"Output reference {output_ref!r} is not declared by upstream node")
        node_payload = upstream_payload.get(reference.node_id, {})
        try:
            resolved_value = _resolve_output_path(node_payload, output_segments)
        except BindingResolutionError:
            if strict_validation:
                raise
            return f"${{{{{output_ref}}}}}"
        consumed.add(output_ref)
        return resolved_value

    def _render_expression_placeholder(expression: _BindingExpression) -> str:
        rendered = f"{expression.primary.node_id}.{expression.primary.output_path}"
        if expression.has_fallback:
            fallback = expression.fallback
            if isinstance(fallback, _BindingReference):
                fallback_text = f"{fallback.node_id}.{fallback.output_path}"
            else:
                fallback_text = json.dumps(fallback)
            rendered = f"{rendered} ?? {fallback_text}"
        return f"${{{{{rendered}}}}}"

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


def _parse_binding_expression(expression: str, *, source_text: str) -> _BindingExpression:
    text = expression.strip()
    if not text:
        raise BindingResolutionError(f"Malformed output reference in string: {source_text!r}")
    primary_text, fallback_text = _split_fallback_expression(text)
    primary = _parse_binding_reference(primary_text, source_text=source_text)
    if fallback_text is None:
        return _BindingExpression(primary=primary)
    fallback_value = _parse_fallback_value(fallback_text, source_text=source_text)
    return _BindingExpression(primary=primary, fallback=fallback_value, has_fallback=True)


def _split_fallback_expression(expression: str) -> tuple[str, str | None]:
    in_string = False
    quote_char = ""
    escape = False
    for index, char in enumerate(expression):
        if in_string:
            if escape:
                escape = False
                continue
            if char == "\\":
                escape = True
                continue
            if char == quote_char:
                in_string = False
                quote_char = ""
            continue
        if char in {'"', "'"}:
            in_string = True
            quote_char = char
            continue
        if expression[index : index + 2] == "??":
            return expression[:index].strip(), expression[index + 2 :].strip()
    return expression.strip(), None


def _parse_binding_reference(reference: str, *, source_text: str) -> _BindingReference:
    if not _OUTPUT_REF_NAME_RE.fullmatch(reference):
        raise BindingResolutionError(f"Malformed output reference in string: {source_text!r}")
    node_id, output_path = reference.split(".", 1)
    return _BindingReference(node_id=node_id, output_path=output_path)


def _parse_fallback_value(value: str, *, source_text: str) -> _BindingReference | Any:
    if not value:
        raise BindingResolutionError(f"Malformed output reference in string: {source_text!r}")
    if _OUTPUT_REF_NAME_RE.fullmatch(value):
        return _parse_binding_reference(value, source_text=source_text)
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise BindingResolutionError(f"Malformed output reference in string: {source_text!r}") from exc


__all__ = [
    "BindingResolutionError",
    "BindingResolutionTrace",
    "BindingResolutionResult",
    "resolve_workflow_bindings",
    "resolve_effect_config_bindings",
    "resolve_gate_map_bindings",
]

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

_OUTPUT_REF_EXACT_RE = re.compile(r"^\$\{\{([^{}]+)\}\}$")
_OUTPUT_REF_ANY_RE = re.compile(r"\$\{\{([^{}]+)\}\}")
_OUTPUT_REF_NAME_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_-]*\.[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*$"
)


class BindingResolutionError(ValueError):
    """Raised when config binding substitution fails validation."""


@dataclass(frozen=True)
class BindingResolutionTraceEntry:
    config_path: str
    expression: str
    reference: str | None = None
    source_node_id: str | None = None
    output_path: str | None = None
    fallback_used: bool = False
    status: str = "resolved"
    resolved_value_preview: str | None = None
    value_type: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class BindingResolutionTrace:
    consumed_output_keys: tuple[str, ...]
    entries: tuple[BindingResolutionTraceEntry, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "consumed_output_keys": list(self.consumed_output_keys),
            "entries": [entry_to_payload(entry) for entry in self.entries],
        }


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
    fallback: _BindingReference | "_BindingExpression" | Any | None = None
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

    return _WorkflowBindingResolver(
        upstream_payload,
        declared_upstream_output_names,
        system_value_resolver=system_value_resolver,
        strict_validation=strict_validation,
    ).resolve(raw_config)


class _WorkflowBindingResolver:
    """Resolve one configuration while keeping traversal and trace state local."""

    def __init__(
        self,
        upstream_payload: dict[str, dict[str, Any]],
        declared_upstream_output_names: dict[str, set[str] | list[str] | tuple[str, ...]],
        *,
        system_value_resolver: Callable[[str], Any] | None,
        strict_validation: bool,
    ) -> None:
        self.upstream_payload = upstream_payload
        self.declared_names = {
            node_id: set(output_names)
            for node_id, output_names in declared_upstream_output_names.items()
        }
        self.system_value_resolver = system_value_resolver
        self.strict_validation = strict_validation
        self.consumed: set[str] = set()
        self.trace_entries: list[BindingResolutionTraceEntry] = []

    def resolve(self, raw_config: Any) -> BindingResolutionResult:
        resolved = self._resolve_value(raw_config)
        trace = BindingResolutionTrace(
            consumed_output_keys=tuple(sorted(self.consumed)),
            entries=tuple(self.trace_entries),
        )
        return BindingResolutionResult(resolved_config=resolved, trace=trace)

    def _resolve_value(self, value: Any, *, config_path: tuple[str | int, ...] = ()) -> Any:
        if isinstance(value, dict):
            resolved_dict: dict[str, Any] = {}
            for key, item in value.items():
                resolved_key = self._resolve_mapping_key(key, config_path=config_path)
                if resolved_key in resolved_dict:
                    formatted_path = _format_config_path(config_path)
                    raise BindingResolutionError(
                        f"Resolved duplicate object key {resolved_key!r} at {formatted_path}"
                    )
                resolved_dict[resolved_key] = self._resolve_value(
                    item, config_path=(*config_path, resolved_key)
                )
            return resolved_dict
        if isinstance(value, list):
            return [
                self._resolve_value(item, config_path=(*config_path, index))
                for index, item in enumerate(value)
            ]
        if isinstance(value, str):
            return self._resolve_string(value, config_path=config_path)
        return value

    def _resolve_string(self, text: str, *, config_path: tuple[str | int, ...]) -> Any:
        exact_match = _OUTPUT_REF_EXACT_RE.fullmatch(text)
        if exact_match:
            expression = _parse_binding_expression(exact_match.group(1), source_text=text)
            return self._consume_expression(
                expression,
                config_path=config_path,
                source_text=text,
            )

        self._validate_malformed_patterns(text)

        def _replace(match: re.Match[str]) -> str:
            expression = _parse_binding_expression(match.group(1), source_text=match.group(0))
            resolved = self._consume_expression(
                expression,
                config_path=config_path,
                source_text=match.group(0),
            )
            return str(resolved)

        return _OUTPUT_REF_ANY_RE.sub(_replace, text)

    def _resolve_mapping_key(self, key: Any, *, config_path: tuple[str | int, ...]) -> str:
        if not isinstance(key, str):
            return str(key)
        if "${{" not in key:
            return key
        resolved = self._resolve_string(key, config_path=config_path)
        if resolved is None or isinstance(resolved, (dict, list, tuple, set)):
            raise BindingResolutionError(
                f"Resolved object key at {_format_config_path(config_path)} must be scalar"
            )
        return str(resolved)

    def _validate_malformed_patterns(self, text: str) -> None:
        if "${{" not in text:
            return
        index = 0
        while True:
            start = text.find("${{", index)
            if start == -1:
                return
            end = text.find("}}", start + 3)
            if end == -1:
                if self.strict_validation:
                    raise BindingResolutionError(f"Malformed output reference in string: {text!r}")
                return
            expression_text = text[start + 3 : end]
            try:
                _parse_binding_expression(expression_text, source_text=text)
            except BindingResolutionError:
                if self.strict_validation:
                    raise BindingResolutionError(f"Malformed output reference in string: {text!r}")
                index = end + 2
                continue
            index = end + 2

    def _consume_expression(
        self,
        expression: _BindingExpression,
        *,
        config_path: tuple[str | int, ...],
        source_text: str,
    ) -> Any:
        try:
            value, resolution = self._resolve_expression(expression)
        except BindingResolutionError:
            if not expression.has_fallback:
                if self.strict_validation:
                    raise
                value = self._render_expression_placeholder(expression)
                resolution = {
                    "reference": f"{expression.primary.node_id}.{expression.primary.output_path}",
                    "source_node_id": expression.primary.node_id,
                    "output_path": expression.primary.output_path,
                    "fallback_used": False,
                    "status": "unresolved",
                }
            else:
                fallback = expression.fallback
                if isinstance(fallback, _BindingExpression):
                    value, resolution = self._resolve_expression(fallback)
                    resolution = {
                        **resolution,
                        "fallback_used": True,
                        "status": "fallback_used",
                    }
                elif isinstance(fallback, _BindingReference):
                    value, reference_text = self._resolve_reference(fallback)
                    resolution = {
                        "reference": reference_text,
                        "source_node_id": fallback.node_id,
                        "output_path": fallback.output_path,
                        "fallback_used": True,
                        "status": "fallback_used",
                    }
                else:
                    value = fallback
                    resolution = {
                        "reference": None,
                        "source_node_id": None,
                        "output_path": None,
                        "fallback_used": True,
                        "status": "fallback_literal",
                    }

        self.trace_entries.append(
            BindingResolutionTraceEntry(
                config_path=_format_config_path(config_path),
                expression=source_text,
                reference=resolution.get("reference"),
                source_node_id=resolution.get("source_node_id"),
                output_path=resolution.get("output_path"),
                fallback_used=bool(resolution.get("fallback_used", False)),
                status=str(resolution.get("status") or "resolved"),
                resolved_value_preview=_preview_value(value),
                value_type=_value_type_name(value),
            )
        )
        return value

    def _resolve_expression(self, expression: _BindingExpression) -> tuple[Any, dict[str, Any]]:
        try:
            value, reference = self._resolve_reference(expression.primary)
        except BindingResolutionError:
            if not expression.has_fallback:
                raise
            fallback = expression.fallback
            if isinstance(fallback, _BindingExpression):
                value, resolution = self._resolve_expression(fallback)
                return value, {
                    **resolution,
                    "fallback_used": True,
                    "status": "fallback_used",
                }
            if isinstance(fallback, _BindingReference):
                value, reference_text = self._resolve_reference(fallback)
                return value, {
                    "reference": reference_text,
                    "source_node_id": fallback.node_id,
                    "output_path": fallback.output_path,
                    "fallback_used": True,
                    "status": "fallback_used",
                }
            return fallback, {
                "reference": None,
                "source_node_id": None,
                "output_path": None,
                "fallback_used": True,
                "status": "fallback_literal",
            }
        return value, {
            "reference": reference,
            "source_node_id": expression.primary.node_id,
            "output_path": expression.primary.output_path,
            "fallback_used": False,
            "status": "resolved",
        }

    def _resolve_reference(self, reference: _BindingReference) -> tuple[Any, str]:
        reference = self._resolve_available_reference(reference)
        output_ref = f"{reference.node_id}.{reference.output_path}"
        output_segments = tuple(segment for segment in reference.output_path.split(".") if segment)
        if not output_segments:
            raise BindingResolutionError(
                f"Malformed output reference in string: '${{{{{output_ref}}}}}'"
            )
        if reference.node_id == "system":
            if self.system_value_resolver is None:
                if self.strict_validation:
                    raise BindingResolutionError(
                        f"Output reference node {reference.node_id!r} is not available in this upstream path"
                    )
                return f"${{{{{output_ref}}}}}", output_ref
            try:
                resolved_value = self.system_value_resolver(reference.output_path)
            except Exception as exc:  # noqa: BLE001
                if self.strict_validation:
                    raise BindingResolutionError(str(exc)) from exc
                return f"${{{{{output_ref}}}}}", output_ref
            self.consumed.add(output_ref)
            return resolved_value, output_ref
        output_name = output_segments[0]
        if self.strict_validation and reference.node_id not in self.declared_names:
            raise BindingResolutionError(
                f"Output reference node {reference.node_id!r} is not available in this upstream path"
            )
        if self.strict_validation and output_name not in self.declared_names.get(reference.node_id, set()):
            raise BindingResolutionError(
                f"Output reference {output_ref!r} is not declared by upstream node"
            )
        node_payload = self.upstream_payload.get(reference.node_id, {})
        try:
            resolved_value = _resolve_output_path(node_payload, output_segments)
        except BindingResolutionError:
            if self.strict_validation:
                raise
            return f"${{{{{output_ref}}}}}", output_ref
        self.consumed.add(output_ref)
        return resolved_value, output_ref

    def _resolve_available_reference(self, reference: _BindingReference) -> _BindingReference:
        full_reference = f"{reference.node_id}.{reference.output_path}"
        segments = tuple(segment for segment in full_reference.split(".") if segment)
        for index in range(len(segments) - 1, 0, -1):
            node_id = ".".join(segments[:index])
            output_path = ".".join(segments[index:])
            if node_id in self.declared_names or node_id in self.upstream_payload:
                return _BindingReference(node_id=node_id, output_path=output_path)
        return reference

    def _render_expression_placeholder(self, expression: _BindingExpression) -> str:
        rendered = f"{expression.primary.node_id}.{expression.primary.output_path}"
        if expression.has_fallback:
            fallback = expression.fallback
            if isinstance(fallback, _BindingExpression):
                fallback_text = _render_expression_text(fallback)
            elif isinstance(fallback, _BindingReference):
                fallback_text = f"{fallback.node_id}.{fallback.output_path}"
            else:
                fallback_text = json.dumps(fallback)
            rendered = f"{rendered} ?? {fallback_text}"
        return f"${{{{{rendered}}}}}"


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
                raise BindingResolutionError(
                    f"Output reference {dotted!r} was not found in upstream payload"
                )
            current = current[segment]
            continue
        if isinstance(current, list):
            if not segment.isdigit():
                dotted = ".".join(traversed)
                raise BindingResolutionError(
                    f"Output reference {dotted!r} was not found in upstream payload"
                )
            index = int(segment)
            if index < 0 or index >= len(current):
                dotted = ".".join(traversed)
                raise BindingResolutionError(
                    f"Output reference {dotted!r} was not found in upstream payload"
                )
            current = current[index]
            continue
        dotted = ".".join(traversed)
        raise BindingResolutionError(
            f"Output reference {dotted!r} was not found in upstream payload"
        )
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
    _fallback_primary, nested_fallback_text = _split_fallback_expression(value)
    if nested_fallback_text is not None:
        return _parse_binding_expression(value, source_text=source_text)
    if _OUTPUT_REF_NAME_RE.fullmatch(value):
        return _parse_binding_reference(value, source_text=source_text)
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise BindingResolutionError(
            f"Malformed output reference in string: {source_text!r}"
        ) from exc


def _render_expression_text(expression: _BindingExpression) -> str:
    rendered = f"{expression.primary.node_id}.{expression.primary.output_path}"
    if expression.has_fallback:
        fallback = expression.fallback
        if isinstance(fallback, _BindingExpression):
            fallback_text = _render_expression_text(fallback)
        elif isinstance(fallback, _BindingReference):
            fallback_text = f"{fallback.node_id}.{fallback.output_path}"
        else:
            fallback_text = json.dumps(fallback)
        rendered = f"{rendered} ?? {fallback_text}"
    return rendered


def _format_config_path(path: tuple[str | int, ...]) -> str:
    if not path:
        return "$"
    rendered = "$"
    for segment in path:
        if isinstance(segment, int):
            rendered = f"{rendered}[{segment}]"
            continue
        if rendered == "$":
            rendered = f"{rendered}.{segment}"
        else:
            rendered = f"{rendered}.{segment}"
    return rendered


def _preview_value(value: Any) -> str | None:
    try:
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        encoded = repr(value)
    if len(encoded) <= 160:
        return encoded
    return f"{encoded[:157]}..."


def _value_type_name(value: Any) -> str | None:
    if value is None:
        return None
    return type(value).__name__


def entry_to_payload(entry: BindingResolutionTraceEntry) -> dict[str, Any]:
    return {
        "config_path": entry.config_path,
        "expression": entry.expression,
        "reference": entry.reference,
        "source_node_id": entry.source_node_id,
        "output_path": entry.output_path,
        "fallback_used": entry.fallback_used,
        "status": entry.status,
        "resolved_value_preview": entry.resolved_value_preview,
        "value_type": entry.value_type,
        "error": entry.error,
    }


def binding_trace_to_payload(trace: BindingResolutionTrace | None) -> dict[str, Any] | None:
    if trace is None:
        return None
    return trace.to_payload()


__all__ = [
    "BindingResolutionError",
    "BindingResolutionTraceEntry",
    "BindingResolutionTrace",
    "BindingResolutionResult",
    "binding_trace_to_payload",
    "entry_to_payload",
    "resolve_workflow_bindings",
    "resolve_effect_config_bindings",
    "resolve_gate_map_bindings",
]

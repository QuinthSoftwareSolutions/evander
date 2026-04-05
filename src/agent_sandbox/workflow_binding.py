from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_OUTPUT_REF_EXACT_RE = re.compile(r"^\$\(([A-Za-z_][A-Za-z0-9_]*)\)$")
_OUTPUT_REF_ANY_RE = re.compile(r"\$\(([A-Za-z_][A-Za-z0-9_]*)\)")
_OUTPUT_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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
    upstream_payload: dict[str, Any],
    declared_upstream_output_names: set[str] | list[str] | tuple[str, ...],
    *,
    strict_validation: bool = True,
) -> BindingResolutionResult:
    """Resolve `$(name)` bindings in workflow config against upstream payload values."""

    declared_names = set(declared_upstream_output_names)
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
            output_name = exact_match.group(1)
            return _consume_output(output_name)

        _validate_malformed_patterns(text)

        def _replace(match: re.Match[str]) -> str:
            output_name = match.group(1)
            return str(_consume_output(output_name))

        return _OUTPUT_REF_ANY_RE.sub(_replace, text)

    def _validate_malformed_patterns(text: str) -> None:
        if "$" not in text:
            return
        index = 0
        while True:
            start = text.find("$(", index)
            if start == -1:
                return
            end = text.find(")", start + 2)
            if end == -1:
                if strict_validation:
                    raise BindingResolutionError(f"Malformed output reference in string: {text!r}")
                return
            name = text[start + 2 : end]
            if not _OUTPUT_NAME_RE.fullmatch(name):
                if strict_validation:
                    raise BindingResolutionError(f"Malformed output reference in string: {text!r}")
                index = end + 1
                continue
            index = end + 1

    def _consume_output(output_name: str) -> Any:
        if strict_validation and output_name not in declared_names:
            raise BindingResolutionError(f"Output reference {output_name!r} is not declared by upstream node")
        if output_name not in upstream_payload:
            if strict_validation:
                raise BindingResolutionError(f"Output reference {output_name!r} was not found in upstream payload")
            return f"$({output_name})"
        consumed.add(output_name)
        return upstream_payload[output_name]

    resolved = _resolve_value(raw_config)
    trace = BindingResolutionTrace(consumed_output_keys=tuple(sorted(consumed)))
    return BindingResolutionResult(resolved_config=resolved, trace=trace)


resolve_effect_config_bindings = resolve_workflow_bindings
resolve_gate_map_bindings = resolve_workflow_bindings

__all__ = [
    "BindingResolutionError",
    "BindingResolutionTrace",
    "BindingResolutionResult",
    "resolve_workflow_bindings",
    "resolve_effect_config_bindings",
    "resolve_gate_map_bindings",
]

from __future__ import annotations

from typing import Literal, cast

AdoQueryMode = Literal["structured_filters", "manual_wiql"]
ADO_QUERY_MODE_STRUCTURED_FILTERS: AdoQueryMode = "structured_filters"
ADO_QUERY_MODE_MANUAL_WIQL: AdoQueryMode = "manual_wiql"
ADO_QUERY_MODES = frozenset({ADO_QUERY_MODE_STRUCTURED_FILTERS, ADO_QUERY_MODE_MANUAL_WIQL})


def normalize_ado_query_mode(value: object) -> AdoQueryMode | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text not in ADO_QUERY_MODES:
        raise ValueError("query_mode must be structured_filters or manual_wiql")
    return cast(AdoQueryMode, text)


def resolve_ado_query_mode(*, query_mode: object, wiql: str | None) -> AdoQueryMode:
    normalized = normalize_ado_query_mode(query_mode)
    if normalized is not None:
        return normalized
    return ADO_QUERY_MODE_MANUAL_WIQL if str(wiql or "").strip() else ADO_QUERY_MODE_STRUCTURED_FILTERS


def validate_ado_query_mode(
    *,
    query_mode: object,
    wiql: str | None,
    current_iteration: bool = False,
    context: str,
) -> AdoQueryMode:
    explicit_mode = normalize_ado_query_mode(query_mode)
    resolved_mode = explicit_mode or resolve_ado_query_mode(query_mode=None, wiql=wiql)
    has_wiql = bool(str(wiql or "").strip())
    if resolved_mode == ADO_QUERY_MODE_MANUAL_WIQL:
        if not has_wiql:
            raise ValueError(f"{context} query_mode=manual_wiql requires wiql")
        if current_iteration:
            raise ValueError(f"{context} query_mode=manual_wiql cannot use current_iteration")
    if explicit_mode == ADO_QUERY_MODE_STRUCTURED_FILTERS and has_wiql:
        raise ValueError(f"{context} query_mode=structured_filters cannot use wiql")
    return resolved_mode

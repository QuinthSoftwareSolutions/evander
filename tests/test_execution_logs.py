"""Log pages preserve line/filter semantics without retaining the full history."""

import random
import tracemalloc

import pytest

from evander.execution import logs
from evander.execution.logs import log_page_from_end


def _expected_page(text, *, limit, offset, query):
    normalized_query = str(query or "").strip()
    lines = text.splitlines()
    if normalized_query:
        lines = [line for line in lines if normalized_query.casefold() in line.casefold()]
    limit = max(1, limit)
    offset = max(0, offset)
    end = max(0, len(lines) - offset)
    start = max(0, end - limit)
    selected = lines[start:end]
    return {
        "text": "\n".join(selected) + ("\n" if selected else ""),
        "limit": limit,
        "offset": offset,
        "returned_lines": len(selected),
        "total_lines": len(lines),
        "start_line": start + 1 if selected else 0,
        "end_line": end if selected else 0,
        "has_more_before": start > 0,
        "query": normalized_query or None,
    }


@pytest.mark.parametrize(
    "separator",
    ["\n", "\r\n", "\r", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"],
)
@pytest.mark.parametrize("query", [None, "  STRASSE  "])
def test_log_page_preserves_empty_lines_unicode_search_and_offsets(separator, query):
    text = separator.join(["", "Straße first", "info", "", "STRASSE last", ""])
    for limit in (0, 1, 3, 20):
        for offset in (-1, 0, 1, 4, 20):
            assert log_page_from_end(
                text, limit=limit, offset=offset, query=query
            ) == _expected_page(text, limit=limit, offset=offset, query=query)


@pytest.mark.parametrize("chunk_size", [1, 2, 7, 64])
def test_log_pages_match_splitlines_for_mixed_separators_and_unterminated_rows(
    monkeypatch, chunk_size
):
    monkeypatch.setattr(logs, "_LINE_CHUNK_SIZE", chunk_size)
    rng = random.Random(490)
    for _ in range(150):
        text = "".join(
            rng.choice(["", "a", "B", "Straße", "\n", "\r", "\u2028", "\x85"])
            for _ in range(rng.randrange(80))
        )
        limit = rng.randrange(1, 10)
        offset = rng.randrange(15)
        query = rng.choice([None, "a", "b", " strasse "])
        assert log_page_from_end(text, limit=limit, offset=offset, query=query) == _expected_page(
            text, limit=limit, offset=offset, query=query
        )


@pytest.mark.parametrize("query", [None, "line"])
def test_log_page_handles_crlf_and_long_lines_crossing_chunk_boundaries(monkeypatch, query):
    monkeypatch.setattr(logs, "_LINE_CHUNK_SIZE", 8)
    text = "line123\r\nline" + "x" * 35 + "\u2028\nlast line\r\npartial line"
    assert log_page_from_end(text, limit=3, offset=1, query=query) == _expected_page(
        text, limit=3, offset=1, query=query
    )


@pytest.mark.parametrize("query", [None, "WARN"])
@pytest.mark.parametrize("limit,offset", [(2, 2**63), (2**63, 0), (2**63, 2**63)])
def test_log_page_accepts_unbounded_api_offsets_and_limits(query, limit, offset):
    text = "first\r\nWARN last\n"
    assert log_page_from_end(text, limit=limit, offset=offset, query=query) == _expected_page(
        text, limit=limit, offset=offset, query=query
    )


@pytest.mark.performance
@pytest.mark.parametrize("query", [None, "INFO"])
def test_small_log_page_does_not_allocate_all_historical_rows(query):
    text = "INFO historical work item\n" * 100_000 + "INFO final work item\n"
    tracemalloc.start()
    try:
        page = log_page_from_end(text, limit=2, offset=0, query=query)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert page["text"] == "INFO historical work item\nINFO final work item\n"
    assert page["total_lines"] == 100_001
    # Retain the selected page, rather than millions of bytes of split strings.
    assert peak < 500_000

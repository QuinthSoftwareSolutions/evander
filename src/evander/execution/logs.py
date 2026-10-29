"""Log pagination used by execution backends and operator APIs."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterator
from itertools import islice


_OTHER_LINE_BREAKS = "\r\v\f\x1c\x1d\x1e\x85\u2028\u2029"
_LINE_BREAKS = "\n" + _OTHER_LINE_BREAKS
_LINE_CHUNK_SIZE = 64 * 1024


def _log_line_chunks(text: str) -> Iterator[list[str]]:
    """Keep C splitlines throughput while retaining only one bounded chunk.

    A line may cross chunks. Collect its fragments until it finishes rather
    than repeatedly copying a growing partial line. Keep CRLF in one chunk.
    """
    start = 0
    partial: list[str] = []
    while start < len(text):
        end = min(len(text), start + _LINE_CHUNK_SIZE)
        if end < len(text) and text[end - 1] == "\r" and text[end] == "\n":
            end += 1
        chunk = text[start:end]
        lines = chunk.splitlines()
        remainder = lines.pop() if chunk[-1] not in _LINE_BREAKS else None
        if lines:
            if partial:
                partial.append(lines[0])
                lines[0] = "".join(partial)
                partial.clear()
            yield lines
        if remainder is not None:
            partial.append(remainder)
        start = end
    if partial:
        yield ["".join(partial)]


def log_page_from_end(
    text: str,
    *,
    limit: int,
    offset: int,
    query: str | None = None,
) -> dict[str, object]:
    normalized_query = str(query or "").strip()
    normalized_limit = max(1, int(limit))
    normalized_offset = max(0, int(offset))
    # Normal worker logs use LF. Count in C, then scan backwards only as far
    # as the requested page instead of allocating a string for every old row.
    if not normalized_query and not any(char in text for char in _OTHER_LINE_BREAKS):
        total_lines = text.count("\n") + int(bool(text) and not text.endswith("\n"))
        end_index = max(0, total_lines - normalized_offset)
        start_index = max(0, end_index - normalized_limit)
        returned_lines = end_index - start_index
        page_text = ""
        if returned_lines:
            end = len(text) - int(text.endswith("\n"))
            for _ in range(normalized_offset):
                end = text.rfind("\n", 0, end)
            start = end
            for _ in range(returned_lines):
                boundary = text.rfind("\n", 0, start)
                if boundary < 0:
                    start = 0
                    break
                start = boundary
            else:
                start += 1
            page_text = text[start:end] + "\n"
    else:
        needle = normalized_query.casefold()
        # API offsets are unbounded integers; the text cannot contain more
        # logical lines than characters, even when it consists only of breaks.
        tail: deque[str] = deque(maxlen=min(len(text), normalized_limit + normalized_offset))
        total_lines = 0
        for lines in _log_line_chunks(text):
            if needle:
                lines = [line for line in lines if needle in line.casefold()]
            total_lines += len(lines)
            tail.extend(lines)
        end_index = max(0, total_lines - normalized_offset)
        start_index = max(0, end_index - normalized_limit)
        returned_lines = end_index - start_index
        page_text = "\n".join(islice(tail, returned_lines)) + ("\n" if returned_lines else "")
    return {
        "text": page_text,
        "limit": normalized_limit,
        "offset": normalized_offset,
        "returned_lines": returned_lines,
        "total_lines": total_lines,
        "start_line": start_index + 1 if returned_lines else 0,
        "end_line": end_index if returned_lines else 0,
        "has_more_before": start_index > 0,
        "query": normalized_query or None,
    }

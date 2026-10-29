"""Bounded decoding and canonical encoding for relay URL path segments."""

from __future__ import annotations

from urllib.parse import quote, unquote

PATH_SEGMENT_SAFE_CHARS = ":@!$&'()*+,;=-._~"
MAX_PATH_DECODE_PASSES = 5


def _safe_path_segments(path: object, *, field_name: str) -> list[str]:
    raw_path = str(path or "").lstrip("/")
    if not raw_path:
        return []
    segments: list[str] = []
    for raw_segment in raw_path.split("/"):
        if not raw_segment:
            continue
        segment = _fully_unquote_path_segment(raw_segment, field_name=field_name)
        if segment in {".", ".."}:
            raise ValueError(f"{field_name} must not contain dot segments")
        if "/" in segment or "\\" in segment:
            raise ValueError(f"{field_name} must not contain encoded path separators")
        segments.append(segment)
    return segments


def _fully_unquote_path_segment(segment: str, *, field_name: str) -> str:
    decoded = segment
    for _ in range(MAX_PATH_DECODE_PASSES):
        next_decoded = unquote(decoded)
        if next_decoded == decoded:
            return decoded
        decoded = next_decoded
    if unquote(decoded) != decoded:
        raise ValueError(f"{field_name} is too deeply percent-encoded")
    return decoded


def _path_from_segments(segments: list[str], *, root_if_empty: bool) -> str:
    if not segments:
        return "/" if root_if_empty else ""
    encoded_segments = [quote(segment, safe=PATH_SEGMENT_SAFE_CHARS) for segment in segments]
    return f"/{'/'.join(encoded_segments)}"

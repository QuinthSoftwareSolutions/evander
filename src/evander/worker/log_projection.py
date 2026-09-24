"""Bounded projections of append-only worker transcripts and reverse log tails."""

from __future__ import annotations

import copy
import json
from array import array
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Hashable

_BLOCK_SIZE = 64 * 1024
_ANCHOR_SIZE = 4096


def _signature(stat) -> tuple[int, int, int, int, int]:
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


@dataclass
class _Projection:
    signatures: tuple[tuple[int, ...], ...]
    offset: int
    prefix: bytes
    boundary: bytes
    state: Any
    result: Any
    weight: int


class JsonlProjectionCache:
    """Cache derived state, never raw events. Only the last file may append.

    Changed older files, replacement, truncation and same-size rewrites rebuild.
    Prefix/boundary checks protect the common truncate-and-regrow case. Writers
    of these transcripts append records; arbitrary edits to old records must
    replace the file, as a stat cannot distinguish those from an append.
    """

    def __init__(self, *, max_entries: int = 64, max_bytes: int = 32 * 1024 * 1024):
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._entries: OrderedDict[Hashable, _Projection] = OrderedDict()
        self._bytes = 0
        self._lock = RLock()
        self._projection_locks: dict[Hashable, tuple[Any, int]] = {}

    @contextmanager
    def _projection_lock(self, key: Hashable):
        # A cold transcript must not serialize unrelated warm projections.
        with self._lock:
            lock, users = self._projection_locks.get(key, (RLock(), 0))
            self._projection_locks[key] = (lock, users + 1)
        try:
            with lock:
                yield
        finally:
            with self._lock:
                _, users = self._projection_locks[key]
                if users == 1:
                    del self._projection_locks[key]
                else:
                    self._projection_locks[key] = (lock, users - 1)

    def read(
        self,
        key: Hashable,
        paths: list[Path],
        *,
        factory: Callable[[], Any],
        consume: Callable[[Any, Any], None],
        weight: Callable[[Any], int],
    ) -> Any:
        cache_key = (key, tuple(paths))
        with self._projection_lock(cache_key):
            present = []
            signatures = []
            for path in paths:
                try:
                    signature = _signature(path.stat())
                except OSError:
                    continue
                present.append(path)
                signatures.append(signature)
            signature_tuple = tuple(signatures)
            with self._lock:
                previous = self._entries.pop(cache_key, None)
                if previous is not None:
                    self._bytes -= previous.weight
                    if previous.signatures == signature_tuple:
                        self._entries[cache_key] = previous
                        self._bytes += previous.weight
            if previous is not None and previous.signatures == signature_tuple:
                return copy.deepcopy(previous.result)
            append = bool(
                previous
                and present
                and len(previous.signatures) == len(signature_tuple)
                and previous.signatures[:-1] == signature_tuple[:-1]
                and previous.signatures[-1][:2] == signature_tuple[-1][:2]
                and previous.signatures[-1][2] < signature_tuple[-1][2]
            )
            if append:
                try:
                    with present[-1].open("rb") as source:
                        prefix = source.read(len(previous.prefix))
                        source.seek(previous.offset - len(previous.boundary))
                        boundary = source.read(len(previous.boundary))
                    append = prefix == previous.prefix and boundary == previous.boundary
                except OSError:
                    append = False
            state = copy.deepcopy(previous.state) if append else factory()
            result = state
            offset = 0
            prefix = boundary = b""
            stable = True
            selected = [(len(present) - 1, present[-1])] if append else list(enumerate(present))
            for index, path in selected:
                try:
                    with path.open("rb") as source:
                        if append:
                            source.seek(previous.offset)
                        while True:
                            start = source.tell()
                            line = source.readline()
                            if not line:
                                offset = start
                                break
                            # Do not commit an unterminated final record: a later
                            # append can still complete/change it. Return its
                            # current valid JSON provisionally, matching full reads.
                            if not line.endswith(b"\n") and index == len(present) - 1:
                                offset = start
                                result = copy.deepcopy(state)
                                _consume_line(result, line, consume)
                                break
                            _consume_line(state, line, consume)
                            result = state
                        if index == len(present) - 1:
                            source.seek(0)
                            prefix = source.read(min(offset, _ANCHOR_SIZE))
                            source.seek(max(0, offset - _ANCHOR_SIZE))
                            boundary = source.read(min(offset, _ANCHOR_SIZE))
                        stable = stable and _signature(path.stat()) == signatures[index]
                except OSError:
                    stable = False
            retained = weight(state) + (weight(result) if result is not state else 0)
            if stable and retained <= self.max_bytes and self.max_entries > 0:
                entry = _Projection(
                    signature_tuple, offset, prefix, boundary, state, result, retained
                )
                with self._lock:
                    self._entries[cache_key] = entry
                    self._bytes += retained
                    while len(self._entries) > self.max_entries or self._bytes > self.max_bytes:
                        _, evicted = self._entries.popitem(last=False)
                        self._bytes -= evicted.weight
            return copy.deepcopy(result)


def _consume_line(state: Any, line: bytes, consume: Callable[[Any, Any], None]) -> None:
    try:
        event = json.loads(line)
    except (ValueError, UnicodeDecodeError):
        return
    consume(state, event)


def read_log_tail(path: Path, lines: int) -> str:
    """Return the same normalized tail as read_text().splitlines() without full I/O."""
    with path.open("rb") as source:
        source.seek(0, 2)
        position = source.tell()
        blocks = []
        newlines = 0
        while position and newlines <= lines:
            size = min(position, _BLOCK_SIZE)
            position -= size
            source.seek(position)
            block = source.read(size)
            blocks.append(block)
            newlines += block.count(b"\n")
        raw = b"".join(reversed(blocks))
        if position:
            # Discard only the leading partial line, also avoiding partial UTF-8.
            raw = raw.split(b"\n", 1)[1]
        text = raw.decode("utf-8")
        return "\n".join(text.splitlines()[-lines:]) + ("\n" if text else "")


def _overlap_length(existing: str, tail: str) -> int:
    """Longest suffix/prefix overlap in O(len(existing) + len(tail))."""
    prefix = array("I", [0]) * len(tail)
    matched = 0
    for index in range(1, len(tail)):
        while matched and tail[index] != tail[matched]:
            matched = prefix[matched - 1]
        if tail[index] == tail[matched]:
            matched += 1
        prefix[index] = matched
    matched = 0
    for char in existing:
        while matched and (matched == len(tail) or char != tail[matched]):
            matched = prefix[matched - 1]
        if char == tail[matched]:
            matched += 1
    return matched


_merge_lock = RLock()


def merge_tail_log_snapshot(path: Path, tail: str) -> None:
    if not tail:
        return
    encoded = tail.encode("utf-8")
    with _merge_lock:
        try:
            source = path.open("r+b")
        except FileNotFoundError:
            path.write_text(tail, encoding="utf-8")
            return
        with source:
            source.seek(0, 2)
            size = source.tell()
            # Four bytes per character, plus a UTF-8 boundary, bounds the suffix
            # needed to test all possible overlaps without reading the full log.
            start = max(0, size - len(tail) * 4 - 4)
            source.seek(start)
            raw = source.read()
            if start:
                while raw and raw[0] & 0xC0 == 0x80:
                    raw = raw[1:]
            suffix = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
            if start == 0 and (not suffix or tail.startswith(suffix)):
                source.seek(0)
                source.write(encoded)
                source.truncate()
                return
            overlap = _overlap_length(suffix[-len(tail) :], tail)
            if overlap:
                remainder = tail[overlap:].encode("utf-8")
                if raw.endswith(b"\r") and remainder.startswith(b"\n"):
                    # read_text() treats an old bare CR as a whole newline.
                    # Give it an extra LF to absorb so the remainder's leading
                    # newline stays distinct, without rewriting the existing log.
                    remainder = b"\n" + remainder
                source.seek(0, 2)
                source.write(remainder)
                return
            # Match the legacy rstrip("\n") behavior without rewriting old text.
            end = size
            while end:
                begin = max(0, end - _BLOCK_SIZE)
                source.seek(begin)
                block = source.read(end - begin)
                trimmed = block.rstrip(b"\r\n")
                end = begin + len(trimmed)
                if trimmed:
                    break
            source.seek(end)
            source.write(b"\n" + encoded)
            source.truncate()

"""Read the navigation subset of SCIP's protobuf wire format, without executing code.

Wire field numbers follow https://github.com/sourcegraph/scip/blob/main/scip.proto.
Unknown fields are skipped; malformed lengths and ranges fail the import.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator

from .models import Occurrence, Relationship, source_path


def _varint(data: bytes, offset: int) -> tuple[int, int]:
    result = 0
    for shift in range(0, 70, 7):
        if offset >= len(data):
            raise ValueError("Truncated SCIP varint")
        byte = data[offset]
        offset += 1
        result |= (byte & 127) << shift
        if not byte & 128:
            return result, offset
    raise ValueError("Invalid SCIP varint")


def _fields(data: bytes) -> Iterator[tuple[int, int | bytes]]:
    offset = 0
    while offset < len(data):
        tag, offset = _varint(data, offset)
        number, wire = tag >> 3, tag & 7
        if number == 0:
            raise ValueError("Invalid SCIP field")
        if wire == 0:
            value, offset = _varint(data, offset)
            yield number, value
        else:
            if wire == 2:
                size, offset = _varint(data, offset)
            elif wire in (1, 5):
                size = 8 if wire == 1 else 4
            else:
                raise ValueError("Unsupported SCIP wire type")
            if offset + size > len(data):
                raise ValueError("Truncated SCIP field")
            yield number, data[offset : offset + size]
            offset += size


def _message(data: bytes) -> dict[int, list]:
    result = defaultdict(list)
    for number, value in _fields(data):
        result[number].append(value)
    return result


def _text(message: dict, field: int, default: str = "") -> str:
    value = message.get(field, [default.encode()])[-1]
    if not isinstance(value, bytes):
        raise ValueError("Invalid SCIP text field")
    return value.decode("utf-8")


def _range(message: dict) -> list[int]:
    if 9 in message:
        typed = _message(message[9][-1])
        values = [typed.get(i, [0])[-1] for i in range(1, 5)]
    elif 8 in message:
        typed = _message(message[8][-1])
        values = [typed.get(i, [0])[-1] for i in range(1, 4)]
    else:
        values = []
        for packed in message.get(1, []):
            if isinstance(packed, int):
                values.append(packed)
            else:
                offset = 0
                while offset < len(packed):
                    value, offset = _varint(packed, offset)
                    values.append(value)
    if len(values) == 3:
        values = [values[0], values[1], values[0], values[2]]
    if (
        len(values) != 4
        or any(not isinstance(v, int) or v < 0 or v > 2**31 - 1 for v in values)
        or values[:2] > values[2:]
    ):
        raise ValueError("Invalid SCIP source range")
    return values


def read_scip(
    data: bytes, *, prefix: str = ""
) -> tuple[list[Occurrence], list[Relationship], set[str]]:
    occurrences, relationships, paths = [], [], set()
    for field, raw_document in _fields(data):
        if field != 2:
            continue
        document = _message(raw_document)
        path = source_path(prefix + _text(document, 1))
        paths.add(path)
        encoding = {0: "utf-16", 1: "utf-8", 2: "utf-16", 3: "utf-32"}.get(document.get(6, [0])[-1])
        if encoding is None:
            raise ValueError("Unsupported SCIP position encoding")

        def scoped(symbol: str) -> str:
            return f"{path}:{symbol}" if symbol.startswith("local ") else symbol

        for raw in document.get(2, []):
            item = _message(raw)
            symbol = _text(item, 2)
            if not symbol:
                continue
            start, column, end, end_column = _range(item)
            roles = item.get(3, [0])[-1]
            occurrences.append(
                Occurrence(
                    path=path,
                    symbol=scoped(symbol),
                    line=start + 1,
                    column=column,
                    end_line=end + 1,
                    end_column=end_column,
                    encoding=encoding,
                    definition=bool(roles & 1),
                    generated=bool(roles & 16),
                )
            )
        for raw in document.get(3, []):
            symbol = _message(raw)
            for raw_relationship in symbol.get(4, []):
                relationship = _message(raw_relationship)
                for flag, kind in ((2, "reference"), (3, "implementation"), (5, "definition")):
                    if relationship.get(flag, [0])[-1]:
                        relationships.append(
                            Relationship(
                                symbol=scoped(_text(symbol, 1)),
                                target=scoped(_text(relationship, 1)),
                                kind=kind,
                            )
                        )
    return occurrences, relationships, paths


def _encode_varint(value: int) -> bytes:
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def _encode_field(number: int, data: bytes) -> bytes:
    return _encode_varint(number * 8 + 2) + _encode_varint(len(data)) + data


def portable_scip(data: bytes, allowed_paths: set[str]) -> bytes:
    """Preserve original navigation documents while removing excluded paths and host metadata."""
    metadata = _encode_field(3, b"file:///workspace/")
    output = bytearray(_encode_field(1, metadata))
    for field, value in _fields(data):
        if field == 2:
            document = _message(value)
            if _text(document, 1) in allowed_paths:
                output.extend(_encode_field(2, value))
    return bytes(output)

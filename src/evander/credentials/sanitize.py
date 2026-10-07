from __future__ import annotations

import base64
import json
import math
import re
from decimal import Decimal
from urllib.parse import unquote_to_bytes

from .store import CredentialError


def _base64_chunks(text: str, overlap: int, minimum: int):
    # Decode composite Basic/JWT values as well as standalone credentials. Work in
    # aligned chunks so large encoded build artifacts do not require another full copy.
    for match in re.finditer(r"[A-Za-z0-9+/_-]{" + str(minimum) + r",}={0,2}", text):
        if (match.end() - match.start()) % 4 == 1:
            continue
        carry = b""
        for start in range(match.start(), match.end(), 65536):
            chunk = text[start:min(start + 65536, match.end())]
            try:
                decoded = base64.b64decode(chunk + "=" * (-len(chunk) % 4), altchars=b"-_", validate=True)
            except ValueError:
                break
            combined = carry + decoded
            yield combined.decode("utf-8", errors="replace")
            carry = combined[-overlap:]


def _representations(text: str):
    # Decode only escaped runs. urllib's Unicode segmentation of an entire
    # decoded binary artifact otherwise performs work for every non-ASCII byte.
    def percent_decode(candidate):
        return re.sub(r"(?:%[0-9a-fA-F]{2})+",
                      lambda match: unquote_to_bytes(match.group()).decode("utf-8", errors="replace"),
                      candidate)

    percent = percent_decode(text) if "%" in text else text
    form = percent_decode(text.replace("+", " ")) if "+" in text else percent
    return dict.fromkeys((text, percent, form))


def reject_reflected_credentials(value, secrets: tuple[str, ...]) -> None:
    """Reject known reflected managed values before result/artifact persistence.

    This is a boundary check for values used by this request, not a scanner for
    arbitrary secrets, encrypted archives, or arbitrary application output.
    """
    secrets = tuple(secret for secret in secrets if secret)
    if not secrets:
        return
    variants = []
    for secret in secrets:
        encoded = base64.b64encode(secret.encode()).decode()
        url_encoded = base64.urlsafe_b64encode(secret.encode()).decode()
        variants.extend((
            secret,
            json.dumps(secret)[1:-1],
            encoded,
            encoded.rstrip("="),
            url_encoded,
            url_encoded.rstrip("="),
        ))
    variants = tuple(candidate for candidate in variants if candidate)
    overlap = max([len(candidate.encode()) for candidate in variants]
                  + [3 * len(secret.encode()) + 2 for secret in secrets])
    minimum = min(max(4, 4 * ((len(secret.encode()) + 2) // 3)) for secret in secrets)
    if any(re.fullmatch(r"-?[0-9]+", secret) for secret in secrets):
        minimum = 4  # Numeric JSON claims can use a shorter exponent representation.

    def inspect(candidate):
        if isinstance(candidate, bytes):
            text = candidate.decode("utf-8", errors="replace")
        elif isinstance(candidate, str):
            text = candidate
        elif isinstance(candidate, dict):
            for key, item in candidate.items():
                inspect(str(key))
                inspect(item)
            return
        elif isinstance(candidate, (tuple, list)):
            for item in candidate:
                inspect(item)
            return
        elif candidate is None or isinstance(candidate, (bool, int, float)):
            text = json.dumps(candidate)
            if isinstance(candidate, float) and math.isfinite(candidate):
                # Parsed JSON can hide a numeric credential behind exponent notation.
                text += " " + format(Decimal(str(candidate)), "f")
        else:
            return

        for representation in _representations(text):
            if any(secret in representation for secret in variants):
                raise CredentialError("Connector response contains managed credential material")
            for decoded in _base64_chunks(representation, overlap, minimum):
                # Parsed claims cover numeric exponents; recursively decode embedded
                # headers as well. Each Base64 step consumes a smaller representation.
                inspect(decoded)
                try:
                    parsed = json.loads(decoded)
                except (ValueError, RecursionError):
                    continue
                if not isinstance(parsed, str) or parsed != decoded:
                    inspect(parsed)

    inspect(value)

from __future__ import annotations

import base64
import json
import math
from decimal import Decimal
from urllib.parse import unquote, unquote_plus

from .store import CredentialError


def reject_reflected_credentials(value, secrets: tuple[str, ...]) -> None:
    """Reject known reflected managed values before result/artifact persistence.

    This is a boundary check for values used by this request, not a scanner for
    arbitrary secrets, encrypted archives, or arbitrary application output.
    """
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    elif isinstance(value, str):
        text = value
    elif isinstance(value, dict):
        for key, item in value.items():
            reject_reflected_credentials(str(key), secrets)
            reject_reflected_credentials(item, secrets)
        return
    elif isinstance(value, (tuple, list)):
        for item in value:
            reject_reflected_credentials(item, secrets)
        return
    elif value is None or isinstance(value, (bool, int, float)):
        text = json.dumps(value)
        if isinstance(value, float) and math.isfinite(value):
            # Parsed JSON can hide a numeric credential behind exponent notation.
            text += " " + format(Decimal(str(value)), "f")
    else:
        return
    # Decode percent escapes, including unreserved bytes and form-encoded spaces.
    representations = (text, unquote(text), unquote_plus(text))
    for secret in secrets:
        encoded = base64.b64encode(secret.encode()).decode()
        url_encoded = base64.urlsafe_b64encode(secret.encode()).decode()
        variants = (
            secret,
            json.dumps(secret)[1:-1],
            encoded,
            encoded.rstrip("="),
            url_encoded,
            url_encoded.rstrip("="),
        )
        if any(
            candidate and candidate in representation
            for representation in representations
            for candidate in variants
        ):
            raise CredentialError("Connector response contains managed credential material")

from __future__ import annotations

import base64
import json
from urllib.parse import quote

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
    else:
        return
    for secret in secrets:
        variants = (
            secret,
            quote(secret, safe=""),
            json.dumps(secret)[1:-1],
            base64.b64encode(secret.encode()).decode(),
        )
        if any(candidate and candidate in text for candidate in variants):
            raise CredentialError("Connector response contains managed credential material")

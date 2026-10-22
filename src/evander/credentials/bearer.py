"""Destination-bound bearer resolution shared by host integrations and cleanup."""

import httpx

from .store import CredentialError, CredentialUnavailable


def resolve_bearer(store, reference, *, directory, binding, url):
    """Authorize the actual request destination before decrypting a resource token."""
    try:
        approved = httpx.URL(binding["destination"])
        actual = httpx.URL(url)
        base = str(approved).rstrip("/")
        if (
            approved.scheme not in {"http", "https"}
            or approved.userinfo
            or approved.query
            or approved.fragment
            or actual.userinfo
            or actual.query
            or actual.fragment
            or not str(actual).startswith(base + "/")
        ):
            raise CredentialUnavailable("Credential destination is not authorized")
        values, _ = store.resolve(reference, directory=directory, binding=binding, kind="resource")
        token = values.get("token")
        if (
            set(values) != {"token"}
            or not isinstance(token, str)
            or not 1 <= len(token) <= 8192
            or "\r" in token
            or "\n" in token
        ):
            raise CredentialUnavailable("Bearer credential is unavailable")
        return token
    except (KeyError, TypeError, ValueError, httpx.InvalidURL):
        raise CredentialUnavailable("Credential destination is not authorized") from None
    except CredentialError:
        raise

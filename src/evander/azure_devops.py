"""Safe Azure DevOps organization identity handling.

Credentialed Azure DevOps clients must always derive their upstream origin from
an organization identifier, rather than accepting a caller-provided URL.  The
legacy URL setting is retained as a migration input only.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit


_ADO_ORGANIZATION_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?")
_DEV_AZURE_HOST = "dev.azure.com"
_VISUAL_STUDIO_SUFFIX = ".visualstudio.com"


def normalize_ado_organization(value: Any, *, field_name: str = "ado_organization") -> str | None:
    """Return a safe Azure DevOps organization identifier.

    This deliberately accepts an identifier only.  URL-shaped values are not
    identifiers and must go through the strictly validated legacy migration
    function below.
    """

    if value is None:
        return None
    organization = str(value).strip()
    if not organization:
        return None
    if not _ADO_ORGANIZATION_RE.fullmatch(organization):
        raise ValueError(f"{field_name} must be an Azure DevOps organization identifier, not a URL")
    return organization


def ado_organization_from_legacy_url(
    value: Any,
    *,
    field_name: str = "azure_devops_organization_url",
) -> str | None:
    """Extract an organization from a supported Azure DevOps public URL.

    The root ``https://dev.azure.com`` is a valid legacy default but does not
    identify an organization, so it returns ``None``.  No arbitrary host,
    userinfo, port, query string, fragment, or extra path segment is accepted.
    """

    if value is None:
        return None
    raw_url = str(value).strip()
    if not raw_url:
        return None
    parsed = urlsplit(raw_url)
    if (
        parsed.scheme.casefold() != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.port is not None
    ):
        raise ValueError(f"{field_name} must be a supported HTTPS Azure DevOps URL")

    hostname = (parsed.hostname or "").casefold()
    if hostname == _DEV_AZURE_HOST:
        parts = [part for part in parsed.path.split("/") if part]
        if not parts:
            return None
        if len(parts) != 1:
            raise ValueError(f"{field_name} must identify exactly one Azure DevOps organization")
        return normalize_ado_organization(parts[0], field_name=field_name)

    if hostname.endswith(_VISUAL_STUDIO_SUFFIX):
        organization = hostname[: -len(_VISUAL_STUDIO_SUFFIX)]
        if not organization or parsed.path.rstrip("/"):
            raise ValueError(f"{field_name} must identify exactly one Azure DevOps organization")
        return normalize_ado_organization(organization, field_name=field_name)

    raise ValueError(f"{field_name} must use dev.azure.com or *.visualstudio.com")


def azure_devops_organization_url(organization: str) -> str:
    """Construct the only credentialed Azure DevOps upstream origin."""

    normalized = normalize_ado_organization(organization)
    if normalized is None:
        raise ValueError("ado_organization is required")
    return f"https://{_DEV_AZURE_HOST}/{normalized}"


def resolve_azure_devops_organization_url(
    *,
    ado_organization: Any,
    legacy_organization_url: Any,
    node_type: str,
) -> str:
    """Resolve a node's org override or a migrated configured legacy value.

    Existing persisted workflow nodes may still contain a legacy Azure DevOps
    URL.  They are accepted here only when they pass the same strict migration
    parser used for configuration; an arbitrary origin is never accepted.
    """

    organization = _organization_from_node_value(ado_organization)
    if organization is None:
        organization = ado_organization_from_legacy_url(legacy_organization_url)
    if organization is None:
        raise ValueError(f"{node_type} requires ado_organization for Azure DevOps")
    return azure_devops_organization_url(organization)


def _organization_from_node_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if "://" in text:
        return ado_organization_from_legacy_url(text, field_name="ado_organization")
    return normalize_ado_organization(text)

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any
from urllib.parse import quote, urlparse

import httpx

_ADO_GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_ADO_DESCRIPTOR_RE = re.compile(r"^[A-Za-z0-9]+\.[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class AdoResolvedMention:
    input: str
    id: str
    display_name: str
    unique_name: str | None = None
    descriptor: str | None = None

    def audit_payload(self) -> dict[str, str]:
        payload = {"input": self.input, "id": self.id, "display_name": self.display_name}
        if self.unique_name:
            payload["unique_name"] = self.unique_name
        if self.descriptor:
            payload["descriptor"] = self.descriptor
        return payload


def normalize_ado_mention_refs(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise ValueError("mentions must be a list of strings")
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item).strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(text)
    return normalized


def resolve_ado_mentions(
    *,
    client: httpx.Client,
    organization_url: str,
    pat: str,
    mention_refs: list[str] | None,
) -> list[AdoResolvedMention]:
    refs = normalize_ado_mention_refs(mention_refs)
    if not refs:
        return []
    resolver = AdoMentionResolver(
        client=client,
        organization_url=organization_url,
        pat=pat,
    )
    resolved: list[AdoResolvedMention] = []
    seen_ids: set[str] = set()
    for ref in refs:
        mention = resolver.resolve(ref)
        key = mention.id.casefold()
        if key in seen_ids:
            continue
        seen_ids.add(key)
        resolved.append(mention)
    return resolved


def render_ado_mentions_prefix(
    body_text: str,
    mentions: list[AdoResolvedMention],
) -> str:
    body = str(body_text or "").strip()
    if not mentions:
        return body
    mention_line = " ".join(f"@<{mention.id}>" for mention in mentions)
    if not body:
        return mention_line
    return f"{mention_line}\n\n{body}"


class AdoMentionResolver:
    def __init__(
        self,
        *,
        client: httpx.Client,
        organization_url: str,
        pat: str,
    ) -> None:
        self.client = client
        self.identities_url = f"{_ado_identity_base_url(organization_url)}/_apis/identities"
        self.pat = pat

    def resolve(self, user_ref: str) -> AdoResolvedMention:
        ref = str(user_ref or "").strip()
        if not ref:
            raise ValueError("ADO mention ref must not be empty")
        if _ADO_GUID_RE.match(ref):
            identities = self._read_identities({"identityIds": ref})
            return self._single_identity(ref, identities, lookup_kind="identity id")
        if _ADO_DESCRIPTOR_RE.match(ref) and "@" not in ref:
            identities = self._read_identities({"descriptors": ref})
            try:
                return self._single_identity(ref, identities, lookup_kind="descriptor")
            except ValueError as exc:
                if "Unable to resolve" not in str(exc):
                    raise
        identities = self._read_identities(
            {
                "searchFilter": "General",
                "filterValue": ref,
                "queryMembership": "None",
            }
        )
        return self._single_identity(ref, identities, lookup_kind="search")

    def _read_identities(self, params: dict[str, str]) -> list[dict[str, Any]]:
        response = self.client.get(
            self.identities_url,
            params={**params, "api-version": "7.1-preview.1"},
            auth=("", self.pat),
        )
        response.raise_for_status()
        payload = response.json()
        value = payload.get("value") if isinstance(payload, dict) else None
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict)]

    def _single_identity(
        self,
        user_ref: str,
        identities: list[dict[str, Any]],
        *,
        lookup_kind: str,
    ) -> AdoResolvedMention:
        candidates = [
            resolved
            for identity in identities
            if (resolved := _resolved_mention_from_identity(user_ref, identity)) is not None
        ]
        if not candidates:
            raise ValueError(f"Unable to resolve ADO mention {lookup_kind}: {user_ref}")
        if len(candidates) > 1:
            raise ValueError(f"ADO mention {lookup_kind} is ambiguous: {user_ref}")
        return candidates[0]


def _resolved_mention_from_identity(
    user_ref: str,
    identity: dict[str, Any],
) -> AdoResolvedMention | None:
    if identity.get("isContainer") is True:
        return None
    if identity.get("isActive") is False:
        return None
    identity_id = _identity_text(identity, "id")
    if not identity_id:
        return None
    display_name = (
        _identity_text(identity, "providerDisplayName")
        or _identity_text(identity, "displayName")
        or _identity_text(identity, "customDisplayName")
        or _identity_text(identity, "uniqueName")
        or user_ref
    )
    unique_name = (
        _identity_text(identity, "uniqueName")
        or _identity_property(identity, "Account")
        or _identity_property(identity, "Mail")
        or _identity_property(identity, "SignInAddress")
    )
    descriptor = _identity_text(identity, "descriptor") or _identity_text(
        identity, "subjectDescriptor"
    )
    return AdoResolvedMention(
        input=user_ref,
        id=identity_id,
        display_name=display_name,
        unique_name=unique_name,
        descriptor=descriptor,
    )


def _identity_text(identity: dict[str, Any], key: str) -> str | None:
    text = str(identity.get(key) or "").strip()
    return text or None


def _identity_property(identity: dict[str, Any], key: str) -> str | None:
    properties = identity.get("properties")
    if not isinstance(properties, dict):
        return None
    value = properties.get(key)
    if isinstance(value, dict):
        value = value.get("$value")
    text = str(value or "").strip()
    return text or None


def _ado_identity_base_url(organization_url: str) -> str:
    parsed = urlparse(str(organization_url or "").rstrip("/"))
    if parsed.netloc == "dev.azure.com":
        organization = parsed.path.strip("/").split("/", 1)[0]
        if organization:
            return f"{parsed.scheme}://vssps.dev.azure.com/{quote(organization, safe='')}"
    if parsed.netloc.endswith(".visualstudio.com"):
        organization = parsed.netloc[: -len(".visualstudio.com")]
        if organization:
            return f"{parsed.scheme}://{organization}.vssps.visualstudio.com"
    return f"{parsed.scheme}://{parsed.netloc}"

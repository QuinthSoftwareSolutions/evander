from __future__ import annotations
from typing import Any
import httpx

from evander.ado_mentions import (
    _ADO_GUID_RE,
    _ADO_DESCRIPTOR_RE,
    AdoResolvedMention,
    normalize_ado_mention_refs,
    _resolved_mention_from_identity,
    _ado_identity_base_url,
 )

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

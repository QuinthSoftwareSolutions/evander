from __future__ import annotations
from evander.optional_integrations import optional_azure_devops_class, require_azure_devops_module

from dataclasses import dataclass
import html
import re
from typing import Any
from urllib.parse import quote, urlparse


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


def render_ado_mentions_html_prefix(
    body_html: str,
    mentions: list[AdoResolvedMention],
) -> str:
    body = str(body_html or "").strip()
    if not mentions:
        return body
    mention_line = " ".join(_render_ado_html_mention(mention) for mention in mentions)
    mention_block = f"<p>{mention_line}</p>"
    if not body:
        return mention_block
    return f"{mention_block}\n{body}"


def _render_ado_html_mention(mention: AdoResolvedMention) -> str:
    mention_id = html.escape(mention.id, quote=True)
    display_name = html.escape(
        mention.display_name or mention.unique_name or mention.input or mention.id
    )
    return f'<a href="#" data-vss-mention="version:2.0,{mention_id}">@{display_name}</a>'




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



def resolve_ado_mentions(*args, **kwargs):
    return require_azure_devops_module('evander_azure_devops_addon.mentions').resolve_ado_mentions(*args, **kwargs)

AdoMentionResolver = optional_azure_devops_class('evander_azure_devops_addon.mentions', 'AdoMentionResolver')

"""Discover downloadable resources explicitly embedded in a forwarded copy."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from html.parser import HTMLParser
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote, unquote, urlparse


FORWARDED_ATTACHMENT_KINDS = {"forwarded_hosted_content", "forwarded_file"}


class _Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[dict[str, str]] = []
        self.anchor: dict[str, str] | None = None

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "img" and values.get("src"):
            self.links.append({"url": values["src"], "text": "", "tag": tag})
        elif tag == "a" and values.get("href"):
            self.anchor = {"url": values["href"], "text": "", "tag": tag}
            self.links.append(self.anchor)

    def handle_data(self, data):
        if self.anchor is not None:
            self.anchor["text"] += data

    def handle_endtag(self, tag):
        if tag == "a":
            self.anchor = None


def _is_hosted_content_url(url: str, api_base_url: str) -> bool:
    parsed, base = urlparse(url), urlparse(api_base_url)
    if (
        parsed.scheme != "https"
        or parsed.netloc.lower() != base.netloc.lower()
        or parsed.query
        or parsed.fragment
        or any(unquote(part) in {".", ".."} for part in parsed.path.split("/"))
    ):
        return False
    message_path = (
        r"(?:chats/[^/]+/messages/[^/]+|"
        r"teams/[^/]+/channels/[^/]+/messages/[^/]+(?:/replies/[^/]+)?)"
    )
    return (
        re.fullmatch(
            re.escape(base.path.rstrip("/"))
            + "/"
            + message_path
            + r"/hostedContents/[^/]+/\$value",
            parsed.path,
        )
        is not None
    )


def forwarded_attachment_candidates(
    attachments: list[dict[str, Any]],
    *,
    api_base_url: str,
    is_file_url: Callable[[str], bool],
) -> list[dict[str, Any]]:
    candidates = []
    for attachment in attachments:
        if attachment.get("contentType") != "forwardedMessageReference":
            continue
        try:
            forwarded = json.loads(attachment.get("content") or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(forwarded, dict):
            continue
        body = forwarded.get("originalMessageContent")
        if not isinstance(body, str):
            continue
        parser = _Links()
        parser.feed(body)
        seen = set()
        for link in parser.links:
            url = link["url"].strip()
            try:
                parsed = urlparse(url)
            except ValueError:
                continue
            if (
                not parsed.scheme
                and not parsed.netloc
                and re.fullmatch(r"\.\./hostedContents/[^/]+/\$value", parsed.path)
                and not parsed.query
                and not parsed.fragment
            ):
                chat_id, message_id = (
                    forwarded.get("originalConversationId"),
                    forwarded.get("originalMessageId"),
                )
                if (
                    not isinstance(chat_id, str)
                    or not isinstance(message_id, str)
                    or not chat_id
                    or not message_id
                ):
                    continue
                url = (
                    f"{api_base_url.rstrip('/')}/chats/{quote(chat_id, safe='')}/messages/"
                    f"{quote(message_id, safe='')}/{parsed.path.removeprefix('../')}"
                )
                parsed = urlparse(url)
            hosted = _is_hosted_content_url(url, api_base_url)
            file_link = (
                link["tag"] == "a"
                and parsed.scheme == "https"
                and not parsed.username
                and not parsed.password
                and not parsed.fragment
                and is_file_url(url)
            )
            if (not hosted and not file_link) or url in seen:
                continue
            seen.add(url)
            reference_id = str(attachment.get("id") or "")
            digest = hashlib.sha256(f"{reference_id}\0{url}".encode()).hexdigest()[:24]
            item = {
                "id": f"forwarded-{digest}",
                "contentUrl": url,
                "attachment_kind": "forwarded_hosted_content" if hosted else "forwarded_file",
                "forwarded_reference_id": reference_id,
            }
            if hosted:
                item["name"] = f"forwarded-image-{len(candidates) + 1}"
            else:
                label = link["text"].strip()
                item["name"] = (
                    PurePosixPath(
                        label if PurePosixPath(label).suffix else unquote(parsed.path)
                    ).name
                    or "forwarded-file"
                )
                item["contentType"] = "reference"
            candidates.append(item)
    return candidates

"""Conservative edits of a recorded text region, with durable write reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import json
import re
from typing import Any

import httpx

from evander.integration_services.teams import (
    TeamsAuthenticationError,
    _teams_message_to_html,
    parse_teams_target,
)
from evander.teams_outbound import (
    TeamsOutboundReceipt,
    TeamsOutboundEditState,
    TeamsPendingEdit,
    TeamsImageSlot,
    compose_outbound_body,
    outbound_edit_result,
)


class TeamsMessageEditError(ValueError):
    pass


@dataclass(frozen=True)
class _Token:
    signature: tuple
    start: int
    end: int


class _BodyParser(HTMLParser):
    """Compare HTML structure without changing or reserializing protected markup."""

    _void = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
    _blocks = {
        "div",
        "body",
        "section",
        "p",
        "blockquote",
        "ul",
        "ol",
        "li",
        "pre",
        "hr",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
    }

    def __init__(self, source: str):
        super().__init__(convert_charrefs=True)
        self.source = source
        self.tokens: list[_Token] = []
        self.stack: list[str] = []
        self.lines = [0]
        self.lines.extend(i + 1 for i, char in enumerate(source) if char == "\n")
        self.feed(source)
        self.close()
        if self.stack:
            raise TeamsMessageEditError("Unbalanced Teams body layout")

    def _offset(self):
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def handle_starttag(self, tag, attrs):
        if len({key for key, _ in attrs}) != len(attrs):
            raise TeamsMessageEditError("Ambiguous Teams body attributes")
        start = self._offset()
        self.tokens.append(
            _Token(
                ("start", tag, tuple(sorted(attrs))), start, start + len(self.get_starttag_text())
            )
        )
        if tag not in self._void:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self._void:
            self.stack.pop()
            end = self.tokens[-1].end
            self.tokens.append(_Token(("end", tag), end, end))

    def handle_endtag(self, tag):
        if tag in self._void:
            return
        if not self.stack or self.stack.pop() != tag:
            raise TeamsMessageEditError("Ambiguous Teams body layout")
        start = self._offset()
        self.tokens.append(_Token(("end", tag), start, self.source.index(">", start) + 1))

    def handle_data(self, data):
        # Graph can pretty-print block markup. Spaces within inline content and
        # all code whitespace remain significant.
        if (
            not data.strip()
            and "\n" in data
            and (
                not self.stack
                or self.stack[-1] in {"div", "body", "section", "blockquote", "ul", "ol"}
            )
        ):
            start = self._offset()
            following = self.source[start + len(data) :]
            next_tag = re.match(r"</?([A-Za-z0-9]+)(?:\s|>|/)", following)
            previous = self.tokens[-1].signature if self.tokens else None
            if (
                previous is None or previous[0] in {"start", "end"} and previous[1] in self._blocks
            ) and (not following or next_tag and next_tag.group(1).lower() in self._blocks):
                return
        if data:
            start = self._offset()
            end = self.source.find("<", start)
            self.tokens.append(_Token(("text", data), start, len(self.source) if end < 0 else end))

    def handle_comment(self, data):
        start = self._offset()
        self.tokens.append(_Token(("comment", data), start, self.source.index("-->", start) + 3))

    def unknown_decl(self, data):
        raise TeamsMessageEditError("Unsupported Teams body declaration")

    def handle_decl(self, decl):
        raise TeamsMessageEditError("Unsupported Teams body declaration")

    def handle_pi(self, data):
        raise TeamsMessageEditError("Unsupported Teams body instruction")


def _signature(body: str) -> list[tuple]:
    return [token.signature for token in _BodyParser(body).tokens]


def _same_body(left: str, right: str) -> bool:
    return _signature(left) == _signature(right)


def _author(payload: dict[str, Any]) -> dict[str, str]:
    sender = payload.get("from") or {}
    for kind in ("user", "application", "device"):
        identity = sender.get(kind) or {}
        if identity.get("id"):
            return {"kind": kind, "id": str(identity["id"])}
    return {}


def _protected(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: payload.get(key) or [] for key in ("mentions", "attachments")}


def record_outbound(service, *, payload, target, envelope, message, image_version=None) -> None:
    """An unusual acknowledged layout must never turn a successful POST into a retry."""
    text_html = _teams_message_to_html(message)
    body = payload.get("body") or {}
    body_html = str(body.get("content") or "")
    prefix = suffix = ""
    reason = None
    try:
        if not _author(payload):
            raise TeamsMessageEditError("Provider did not acknowledge a message author")
        if str(body.get("contentType", "")).lower() != "html":
            raise TeamsMessageEditError("Provider did not acknowledge an HTML body")
        tokens = _BodyParser(body_html).tokens
        needle = _signature(text_html)
        signatures = [token.signature for token in tokens]
        matches = [
            i
            for i in range(len(tokens) - len(needle) + 1)
            if signatures[i : i + len(needle)] == needle
        ]
        if len(matches) != 1:
            raise TeamsMessageEditError("Cannot identify a unique editable text region")
        index = matches[0]
        prefix, suffix = (
            body_html[: tokens[index].start],
            body_html[tokens[index + len(needle) - 1].end :],
        )
        if not _same_body(prefix + text_html + suffix, body_html):
            raise TeamsMessageEditError("Provider changed the editable text layout")
    except (ValueError, IndexError) as exc:
        reason = str(exc)
    slot = None
    image_reason = None
    if image_version is not None:
        try:
            if reason:
                raise TeamsMessageEditError("Text layout is not editable")
            # The suffix can close a wrapper opened in the prefix. Parse the
            # complete acknowledged body, then retain suffix-relative spans.
            image_tokens = _BodyParser(body_html).tokens
            suffix_start = len(body_html) - len(suffix)
            matches = [
                i
                for i, t in enumerate(image_tokens)
                if t.signature[:2] == ("start", "img")
                and dict(t.signature[2]).get("src") == image_version.source_url
            ]
            if len(matches) != 1:
                raise TeamsMessageEditError("Cannot identify a unique editable image")
            i = matches[0]
            if (
                i < 1
                or i + 1 >= len(image_tokens)
                or image_tokens[i - 1].signature != ("start", "p", ())
                or image_tokens[i + 1].signature != ("end", "p")
            ):
                raise TeamsMessageEditError("Editable image requires its own paragraph")
            start, end = image_tokens[i - 1].start, image_tokens[i + 1].end
            if start < suffix_start:
                raise TeamsMessageEditError("Editable image is outside its designated suffix")
            slot = TeamsImageSlot(
                before=suffix[: start - suffix_start], after=suffix[end - suffix_start :]
            )
        except (ValueError, IndexError):
            image_reason = "Acknowledged image layout is ambiguous; image editing unavailable"
    receipt = TeamsOutboundReceipt(
        channel_target=target.normalized,
        message_id=envelope.message_id,
        session_id=str(envelope.metadata["session_id"]),
        thread_id=envelope.thread_id,
        author=_author(payload),
        body_prefix=prefix,
        body_suffix=suffix,
        protected=_protected(payload),
        editable=reason is None,
        uneditable_reason=reason,
        web_url=envelope.metadata.get("web_url"),
        image_slot=slot,
        image_uneditable_reason=image_reason,
    )
    service.message_store.create_outbound_receipt(
        receipt,
        TeamsOutboundEditState(
            text_html=text_html, body_html=body_html, image=image_version if slot else None
        ),
    )


def _verify_identity(payload, receipt):
    target = parse_teams_target(receipt.channel_target)
    parent = (payload.get("replyToId") or payload.get("id")) if target.kind == "channel" else None
    if (
        str(payload.get("id") or "") != receipt.message_id
        or parent != receipt.thread_id
        or payload.get("deletedDateTime")
        or _author(payload) != receipt.author
    ):
        raise TeamsMessageEditError(
            "Teams message author or thread no longer matches its outbound receipt"
        )
    if (
        str((payload.get("body") or {}).get("contentType", "")).lower() != "html"
        or _protected(payload) != receipt.protected
    ):
        raise TeamsMessageEditError("Teams message protected content has changed")


def update_message(
    service,
    *,
    session_id,
    channel_target,
    message_id,
    message,
    thread_id=None,
    expected_revision=None,
    image_version=None,
    clear_image=False,
    verify_image_source=None,
):
    target = parse_teams_target(channel_target)
    if target.kind == "user":
        raise TeamsMessageEditError("Use an existing Teams channel or chat target")
    store = service.message_store
    with store.lock_outbound_message(target.normalized, message_id):
        receipt = store.get_outbound_receipt(target.normalized, message_id)
        if receipt is None or receipt.session_id != session_id:
            raise TeamsMessageEditError(
                "No outbound ownership receipt for this session and conversation"
            )
        if (target.kind == "chat" and thread_id is not None) or (
            thread_id is not None and thread_id != receipt.thread_id
        ):
            raise TeamsMessageEditError("thread_id does not match the recorded Teams message")
        if not receipt.editable:
            raise TeamsMessageEditError(f"Teams message is uneditable: {receipt.uneditable_reason}")
        if image_version is not None and clear_image:
            raise TeamsMessageEditError("Replacement image and clear_image are mutually exclusive")
        if (image_version is not None or clear_image) and receipt.image_slot is None:
            raise TeamsMessageEditError("No editable image slot was recorded for this message")
        state = store.get_outbound_edit_state(target.normalized, message_id)
        if state is None:
            raise TeamsMessageEditError("Teams message edit state is missing")
        desired = _teams_message_to_html(message)
        url = service._teams_message_url(target, message_id, thread_id=receipt.thread_id)
        try:
            with httpx.Client(timeout=service.timeout_seconds) as client:

                def inspect():
                    current = service._request_with_retries(client, "GET", url).json()
                    _verify_identity(current, receipt)
                    return current, str(current["body"]["content"])

                current, current_body = inspect()

                def image_key(image):
                    return (image.artifact_id, image.sha256) if image else None

                if state.pending is not None:
                    if _same_body(current_body, state.pending.body_html):
                        if state.pending.image is not None and verify_image_source is not None:
                            verify_image_source(state.pending.image)
                        state = TeamsOutboundEditState(
                            revision=state.revision + 1,
                            text_html=state.pending.text_html,
                            body_html=current_body,
                            image=state.pending.image,
                        )
                        store.save_outbound_edit_state(target.normalized, message_id, state)
                        _cache_current(service, current, target, receipt)
                    elif not _same_body(current_body, state.body_html):
                        raise TeamsMessageEditError(
                            "Pending Teams edit conflicts with an external change; operator reconciliation is required"
                        )
                    elif desired != state.pending.text_html or image_key(
                        image_version
                        if image_version is not None
                        else None
                        if clear_image
                        else state.image
                    ) != image_key(state.pending.image):
                        raise TeamsMessageEditError(
                            "A Teams edit is still pending; retry its original replacement text first"
                        )
                    elif image_version is not None:
                        # A concurrent retry may have issued another URL before
                        # this lock was acquired. Always retry the durable URL.
                        image_version = state.pending.image
                elif not _same_body(current_body, state.body_html):
                    raise TeamsMessageEditError(
                        "Teams message layout or text changed outside this editing service"
                    )
                desired_image = (
                    image_version
                    if image_version is not None
                    else None
                    if clear_image
                    else state.image
                )
                if (
                    state.pending is None
                    and desired == state.text_html
                    and image_key(desired_image) == image_key(state.image)
                ):
                    return _result(receipt, state, "unchanged")
                if expected_revision is not None and expected_revision != state.revision:
                    raise TeamsMessageEditError(
                        f"Stale Teams message revision; current revision is {state.revision}"
                    )
                pending = TeamsPendingEdit(
                    text_html=desired,
                    body_html=compose_outbound_body(receipt, desired, desired_image),
                    image=desired_image,
                )
                state = state.model_copy(update={"pending": pending})
                store.save_outbound_edit_state(target.normalized, message_id, state)
                # Only body is sent. In particular, do not resend or re-resolve
                # mention identities, attachments, hosted content, or card data.
                service._request_with_retries(
                    client,
                    "PATCH",
                    url,
                    json={"body": {"contentType": "html", "content": pending.body_html}},
                )
                current, current_body = inspect()
                if not _same_body(current_body, pending.body_html):
                    raise TeamsMessageEditError(
                        "Teams edit could not be verified; intent retained for reconciliation"
                    )
                if desired_image is not None and verify_image_source is not None:
                    verify_image_source(desired_image)
                state = TeamsOutboundEditState(
                    revision=state.revision + 1,
                    text_html=desired,
                    body_html=current_body,
                    image=desired_image,
                )
                store.save_outbound_edit_state(target.normalized, message_id, state)
                _cache_current(service, current, target, receipt)
                return _result(receipt, state, "updated")
        except TeamsAuthenticationError:
            raise TeamsMessageEditError(
                "Teams edit authentication is unavailable; check the host's configured identity"
            ) from None
        except httpx.HTTPError as exc:
            code = (
                exc.response.status_code
                if isinstance(exc, httpx.HTTPStatusError)
                else "unavailable"
            )
            hint = (
                " Check delegated edit consent and the tenant message editing policy."
                if code == 403
                else ""
            )
            raise TeamsMessageEditError(
                f"Teams message update failed (HTTP {code}); pending intent, if any, is retained.{hint}"
            ) from None
        except json.JSONDecodeError:
            raise TeamsMessageEditError(
                "Teams returned invalid JSON; edit cannot be verified"
            ) from None
        except (KeyError, TypeError):
            raise TeamsMessageEditError(
                "Teams returned an invalid message; edit cannot be verified"
            ) from None


def _cache_current(service, current, target, receipt):
    envelope = service._to_envelope(
        current,
        target=target,
        source="teams_graph_api_status",
        session_id=receipt.session_id,
        reply_to_id=receipt.thread_id if receipt.thread_id != receipt.message_id else None,
    )
    service.message_store.put(envelope.cursor, envelope)


def _result(receipt, state, status):
    return outbound_edit_result(receipt, state, status)

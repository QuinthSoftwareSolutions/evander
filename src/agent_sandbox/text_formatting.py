from __future__ import annotations

import html
import re

_FENCED_CODE_BLOCK_RE = re.compile(r"```[^\n`]*\n?(.*?)```", re.DOTALL)
_UNORDERED_LIST_RE = re.compile(r"^\s*[-*+]\s+(.+)$")
_ORDERED_LIST_RE = re.compile(r"^\s*\d+[.)]\s+(.+)$")
_HEADING_RE = re.compile(r"^\s*(#{1,6})\s+(.+)$")
_BLOCKQUOTE_RE = re.compile(r"^\s{0,3}>\s?(.*)$")
_HORIZONTAL_RULE_RE = re.compile(r"^\s{0,3}([-*_])(?:\s*\1){2,}\s*$")
_HTML_TAG_RE = re.compile(r"<[A-Za-z][^>]*>")


def looks_like_html(text: str | None) -> bool:
    return _HTML_TAG_RE.search(str(text or "")) is not None


def render_markdownish_text_to_html(text: str) -> str:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    parts: list[str] = []
    last_index = 0
    for match in _FENCED_CODE_BLOCK_RE.finditer(normalized):
        if match.start() > last_index:
            parts.append(_text_fragment_to_html(normalized[last_index:match.start()]))
        code_content = match.group(1)
        code_content = code_content.lstrip("\n").rstrip("\n")
        parts.append(f"<pre><code>{html.escape(code_content)}</code></pre>")
        last_index = match.end()
    if last_index < len(normalized):
        parts.append(_text_fragment_to_html(normalized[last_index:]))
    return "".join(fragment for fragment in parts if fragment)


def _text_fragment_to_html(text: str) -> str:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    blocks: list[str] = []
    index = 0

    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue

        heading_match = _HEADING_RE.match(line)
        if heading_match:
            level = len(heading_match.group(1))
            blocks.append(f"<h{level}>{_render_inline(heading_match.group(2))}</h{level}>")
            index += 1
            continue

        if _HORIZONTAL_RULE_RE.match(line):
            blocks.append("<hr>")
            index += 1
            continue

        quote_match = _BLOCKQUOTE_RE.match(line)
        if quote_match:
            quote_lines: list[str] = [quote_match.group(1)]
            index += 1
            while index < len(lines):
                next_match = _BLOCKQUOTE_RE.match(lines[index])
                if not next_match:
                    break
                quote_lines.append(next_match.group(1))
                index += 1
            quote_html = _text_fragment_to_html("\n".join(quote_lines))
            blocks.append(f"<blockquote>{quote_html}</blockquote>")
            continue

        unordered_match = _UNORDERED_LIST_RE.match(line)
        if unordered_match:
            items = [f"<li>{_render_inline(unordered_match.group(1))}</li>"]
            index += 1
            while index < len(lines):
                next_match = _UNORDERED_LIST_RE.match(lines[index])
                if not next_match:
                    break
                items.append(f"<li>{_render_inline(next_match.group(1))}</li>")
                index += 1
            blocks.append(f"<ul>{''.join(items)}</ul>")
            continue

        ordered_match = _ORDERED_LIST_RE.match(line)
        if ordered_match:
            items = [f"<li>{_render_inline(ordered_match.group(1))}</li>"]
            index += 1
            while index < len(lines):
                next_match = _ORDERED_LIST_RE.match(lines[index])
                if not next_match:
                    break
                items.append(f"<li>{_render_inline(next_match.group(1))}</li>")
                index += 1
            blocks.append(f"<ol>{''.join(items)}</ol>")
            continue

        paragraph_lines = [line]
        index += 1
        while index < len(lines):
            candidate = lines[index]
            if not candidate.strip() or _line_starts_block(candidate):
                break
            paragraph_lines.append(candidate)
            index += 1
        paragraph_html = _render_inline("\n".join(paragraph_lines)).replace("\n", "<br>")
        blocks.append(f"<p>{paragraph_html}</p>")

    return "".join(blocks)


def _line_starts_block(line: str) -> bool:
    return (
        _HEADING_RE.match(line) is not None
        or _HORIZONTAL_RULE_RE.match(line) is not None
        or _BLOCKQUOTE_RE.match(line) is not None
        or _UNORDERED_LIST_RE.match(line) is not None
        or _ORDERED_LIST_RE.match(line) is not None
    )


def _render_inline(text: str) -> str:
    rendered = html.escape(text)
    placeholders: list[str] = []

    def _stash(value: str) -> str:
        placeholders.append(value)
        return f"\u0000{len(placeholders) - 1}\u0000"

    rendered = re.sub(
        r"`([^`\n]+?)`",
        lambda match: _stash(f"<code>{match.group(1)}</code>"),
        rendered,
    )
    rendered = re.sub(
        r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
        lambda match: f'<a href="{html.escape(match.group(2), quote=True)}">{match.group(1)}</a>',
        rendered,
    )
    rendered = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"<strong>\1</strong>", rendered)
    rendered = re.sub(r"__(?=\S)(.+?)(?<=\S)__", r"<strong>\1</strong>", rendered)
    rendered = re.sub(r"~~(?=\S)(.+?)(?<=\S)~~", r"<del>\1</del>", rendered)
    rendered = re.sub(r"(?<!\*)\*(?=\S)(.+?)(?<=\S)\*(?!\*)", r"<em>\1</em>", rendered)
    rendered = re.sub(r"(?<!\w)_(?=\S)(.+?)(?<=\S)_(?!\w)", r"<em>\1</em>", rendered)

    return re.sub(r"\u0000(\d+)\u0000", lambda match: placeholders[int(match.group(1))], rendered)

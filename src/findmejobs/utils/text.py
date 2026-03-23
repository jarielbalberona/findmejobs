from __future__ import annotations

import re
from html import unescape

from lxml import html

WHITESPACE_RE = re.compile(r"\s+")
LEGAL_SUFFIX_RE = re.compile(
    r"\b(inc|inc\.|llc|ltd|ltd\.|corp|corp\.|corporation|gmbh|pty|plc)\b",
    re.IGNORECASE,
)


def collapse_whitespace(value: str) -> str:
    return WHITESPACE_RE.sub(" ", value).strip()


def html_to_text(value: str | None) -> str:
    if not value:
        return ""
    normalized = value
    for _ in range(3):
        decoded = unescape(normalized)
        if decoded == normalized:
            break
        normalized = decoded
    try:
        root = html.fromstring(normalized)
        html.etree.strip_elements(root, "script", "style", with_tail=False)
        text = root.text_content()
    except (html.ParserError, ValueError):
        text = normalized
    return collapse_whitespace(text)


def normalize_company_name(value: str) -> str:
    lowered = collapse_whitespace(value).casefold()
    stripped = LEGAL_SUFFIX_RE.sub("", lowered)
    stripped = stripped.strip(" ,.-")
    return collapse_whitespace(stripped)


def normalize_title(value: str) -> str:
    return collapse_whitespace(value).casefold()


def normalize_location(value: str) -> str:
    return collapse_whitespace(value).casefold()


def truncate_text(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return value[: max_length - 3].rstrip() + "..."

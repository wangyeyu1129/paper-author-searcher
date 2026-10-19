"""Shared HTML helpers: encoding-aware decoding of page prefixes and one configurable link extractor."""

import codecs
import re
from collections.abc import Mapping, Sequence
from html.parser import HTMLParser
from typing import Any

BOMS: tuple[tuple[bytes, str], ...] = (
    (codecs.BOM_UTF8, 'utf-8'),
    (codecs.BOM_UTF32_LE, 'utf-32-le'),
    (codecs.BOM_UTF32_BE, 'utf-32-be'),
    (codecs.BOM_UTF16_LE, 'utf-16-le'),
    (codecs.BOM_UTF16_BE, 'utf-16-be'),
)
_HEADER_CHARSET = re.compile(r'charset\s*=\s*["\']?([\w.:-]+)', re.IGNORECASE)
_META_CHARSET = re.compile(rb'<meta[^>]+charset\s*=\s*["\']?\s*([\w.:-]+)', re.IGNORECASE)
META_SCAN_BYTES = 2048


def _known(encoding: str | None) -> str | None:
    if not encoding:
        return None
    try:
        return codecs.lookup(encoding).name
    except LookupError:
        return None


def html_encoding(prefix: bytes, content_type: str | None = None) -> str:
    """BOM, then the Content-Type charset, then <meta charset>, then UTF-8 (the HTML default for the web)."""
    for bom, encoding in BOMS:
        if prefix.startswith(bom):
            return encoding
    header = _HEADER_CHARSET.search(content_type or '')
    declared = _known(header.group(1)) if header else None
    if declared:
        return declared
    meta = _META_CHARSET.search(prefix[:META_SCAN_BYTES])
    found = _known(meta.group(1).decode('ascii', 'ignore')) if meta else None
    return found or 'utf-8'


def decode_html(prefix: bytes, headers: Mapping[str, str] | None = None) -> str:
    """Decode a page prefix in its declared encoding; undecodable bytes become U+FFFD rather than failing."""
    content_type = None
    for name, value in (headers or {}).items():
        if name.lower() == 'content-type':
            content_type = value
            break
    encoding = html_encoding(prefix, content_type)
    text = prefix.decode(encoding, errors='replace')
    return text[1:] if text.startswith('﻿') else text


def decode_response(response: Any) -> str:
    """``decode_html`` for a probe response (``prefix`` bytes and ``headers``)."""
    return decode_html(response.prefix, getattr(response, 'headers', None))


class LinkExtractor(HTMLParser):
    """Anchors with their text and the nearest preceding heading.

    ``heading_tags`` open a heading whose text labels the following links; ``body_tags`` end a heading even when
    its closing tag is omitted (HTML allows ``<dt>`` without ``</dt>``). Optionally also records ``<link href>``
    elements, JSON-LD script bodies and up to ``trailing_context`` characters of text after each link.
    """

    def __init__(
        self,
        heading_tags: Sequence[str] = ('dt',),
        body_tags: Sequence[str] = ('dd',),
        include_link_elements: bool = False,
        capture_json_ld: bool = False,
        trailing_context: int = 0,
    ) -> None:
        super().__init__(convert_charrefs=True)
        self.heading_tags = frozenset(heading_tags)
        self.body_tags = frozenset(body_tags)
        self.include_link_elements = include_link_elements
        self.capture_json_ld = capture_json_ld
        self.trailing_context = trailing_context
        self.links: list[dict[str, Any]] = []
        self.scripts: list[str] = []
        self.heading = ''
        self._in_heading = False
        self._current: dict[str, Any] | None = None
        self._trailing: dict[str, Any] | None = None
        self._script: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = dict(attrs)
        if tag in self.heading_tags:
            self.heading = ''
            self._in_heading = True
        elif tag in self.body_tags:
            self._in_heading = False
        if self.capture_json_ld and tag == 'script' and attributes.get('type') == 'application/ld+json':
            self._script = ''
        if tag == 'a':
            self._trailing = None
            self._current = dict(attributes, text='', context='', heading=self.heading)
        elif tag == 'link' and self.include_link_elements and attributes.get('href'):
            self.links.append(dict(attributes, text='', context='', heading=self.heading))

    def handle_data(self, data: str) -> None:
        if self._script is not None:
            self._script += data
        elif self._in_heading:
            self.heading += data
        if self._current is not None:
            self._current['text'] += data
        elif self._trailing is not None and len(self._trailing['context']) < self.trailing_context:
            self._trailing['context'] += data

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.heading_tags:
            self._in_heading = False
        if tag == 'script' and self._script is not None:
            self.scripts.append(self._script)
            self._script = None
        if tag == 'a' and self._current is not None:
            if self._current.get('href'):
                self.links.append(self._current)
                self._trailing = self._current
            self._current = None

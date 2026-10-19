"""Split a researcher's publication page into citations, so anchors can come from the person's own entries.

Pages differ: some link DOIs, some list only titles (Yi Cui's group page, Raj Chetty's papers page), and lab pages
list everyone's papers (the Yale Lu group lists 95 DOIs, most without the student being benchmarked). Each block
element becomes one citation with its identifiers, its likely title strings and its text, so the anchor finder can
prefer citations that name the person and fall back to title lookups when a page links nothing.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from typing import Protocol

from .config import DEFAULT_ANCHORS, AnchorConfig
from .matching import normalize

DOI_IN_TEXT = re.compile(r'\b10\.\d{4,9}/[^\s"\'<>)\]]+', re.IGNORECASE)
ARXIV_IN_TEXT = re.compile(r'arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})', re.IGNORECASE)
QUOTED = re.compile(r'[“"]([^”"]+)[”"]')
TRAILING_PUNCTUATION = '.,;'


@dataclass(frozen=True)
class HomepageCitation:
    text: str
    dois: tuple[str, ...] = ()
    arxiv_ids: tuple[str, ...] = ()
    titles: tuple[str, ...] = ()


class CitationParser(Protocol):
    def parse(self, html: str) -> list[HomepageCitation]: ...


@dataclass
class _Block:
    position: int
    text: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    emphasised: list[str] = field(default_factory=list)


class _BlockCollector(HTMLParser):
    """Text goes to the innermost open block, so a list item is one citation even inside a container div."""

    def __init__(self, config: AnchorConfig) -> None:
        super().__init__(convert_charrefs=True)
        self.config = config
        self.blocks: list[_Block] = []
        self.finished: list[_Block] = []
        self.emphasis: list[list[str]] = []
        self.opened = 0

    def handle_starttag(self, tag: str, attrs: Sequence[tuple[str, str | None]]) -> None:
        if tag in self.config.citation_block_tags:
            self.blocks.append(_Block(self.opened))
            self.opened += 1
        elif tag in self.config.title_tags:
            self.emphasis.append([])
        if tag == 'a' and self.blocks:
            self.blocks[-1].links.extend(value for name, value in attrs if name == 'href' and value)

    def handle_endtag(self, tag: str) -> None:
        if tag in self.config.citation_block_tags and self.blocks:
            self.finished.append(self.blocks.pop())
        elif tag in self.config.title_tags and self.emphasis:
            text = ' '.join(''.join(self.emphasis.pop()).split())
            if self.blocks and text:
                self.blocks[-1].emphasised.append(text)

    def handle_data(self, data: str) -> None:
        if self.blocks:
            self.blocks[-1].text.append(data)
        for open_emphasis in self.emphasis:
            open_emphasis.append(data)

    def close(self) -> None:
        super().close()
        self.finished.extend(reversed(self.blocks))
        self.blocks = []


class HtmlCitationParser:
    """Default parser: block elements are citations; emphasised or quoted spans of title length are titles."""

    def __init__(self, config: AnchorConfig = DEFAULT_ANCHORS) -> None:
        self.config = config

    def parse(self, html: str) -> list[HomepageCitation]:
        collector = _BlockCollector(self.config)
        collector.feed(html)
        collector.close()
        citations = []
        # Blocks finish innermost first; page order is the order in which they opened.
        for block in sorted(collector.finished, key=lambda item: item.position):
            text = ' '.join(''.join(block.text).split())
            searchable = unescape(' '.join([text, *block.links]))
            dois = _unique(match.group(0).rstrip(TRAILING_PUNCTUATION) for match in DOI_IN_TEXT.finditer(searchable))
            arxiv = _unique(match.group(1) for match in ARXIV_IN_TEXT.finditer(searchable))
            titles = _unique(
                candidate.strip(' .,;:')
                for candidate in [*block.emphasised, *QUOTED.findall(text)]
                if self._title_like(candidate)
            )
            if dois or arxiv or titles:
                citations.append(HomepageCitation(text, tuple(dois), tuple(arxiv), tuple(titles)))
        return citations

    def _title_like(self, value: str) -> bool:
        value = value.strip()
        words = value.split()
        if len(words) < self.config.title_minimum_words or len(value) > self.config.title_maximum_characters:
            return False
        return not DOI_IN_TEXT.search(value) and not value.lower().startswith(('http', 'www.'))


def mentions(name: str, text: str) -> bool:
    """The citation names the person: every name token, or the surname with the other name's initial (either
    order, so "Y. Liu", "Liu Y" and "Yang Liu" all count)."""
    parts = normalize(name).split()
    tokens = set(normalize(text).split())
    if not parts or not tokens:
        return False
    if all(part in tokens for part in parts):
        return True
    first, last = parts[0], parts[-1]
    return (last in tokens and first[0] in tokens) or (first in tokens and last[0] in tokens)


def prioritise(citations: Iterable[HomepageCitation], name: str) -> list[HomepageCitation]:
    """Citations that name the person first, page order otherwise kept: on a lab page they are the person's own."""
    citations = list(citations)
    return [citation for citation in citations if mentions(name, citation.text)] + [
        citation for citation in citations if not mentions(name, citation.text)
    ]


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))

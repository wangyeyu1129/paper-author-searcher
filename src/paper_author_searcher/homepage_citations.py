"""Split a researcher's publication page into citations, so anchors can come from the person's own entries.

Pages differ: some link DOIs, some list only titles (Yi Cui's group page, Raj Chetty's papers page), and lab pages
list everyone's papers (the Yale Lu group lists 95 DOIs, most without the student being benchmarked). Each block
element becomes one citation with its identifiers, its likely title strings and its text, so the anchor finder can
prefer citations that name the person and fall back to title lookups when a page links nothing.
"""

import io
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from html import unescape
from html.parser import HTMLParser
from typing import Any, Protocol
from urllib.parse import urlsplit

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
    # Links of the citation to a PDF file, as written on the page (relative links resolved by the page reader).
    pdf_links: tuple[str, ...] = ()


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
            pdfs = _unique(link for link in block.links if pdf_link(link, self.config))
            if dois or arxiv or titles or pdfs:
                citations.append(HomepageCitation(text, tuple(dois), tuple(arxiv), tuple(titles), tuple(pdfs)))
        return citations

    def _title_like(self, value: str) -> bool:
        return title_like(value, self.config)


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


def pdf_link(url: str, config: AnchorConfig = DEFAULT_ANCHORS) -> bool:
    path = urlsplit(unescape(url)).path.lower()
    return path.endswith(config.pdf_link_suffixes) or any(marker in path for marker in config.pdf_link_path_markers)


def title_like(value: str, config: AnchorConfig = DEFAULT_ANCHORS) -> bool:
    """A span of title length that is not a link or an identifier."""
    value = value.strip()
    if len(value.split()) < config.title_minimum_words or len(value) > config.title_maximum_characters:
        return False
    return not DOI_IN_TEXT.search(value) and not value.lower().startswith(('http', 'www.'))


class PdfCitationParser:
    """A CV or publication list in PDF: each paragraph (or numbered / bulleted / year-led line) is one citation;
    quoted spans of title length are titles. Needs the optional ``pypdf`` dependency (the ``download`` extra)."""

    ENTRY_START = re.compile(r'^\s*(?:\[\d+\]|\d+[.)]|[•●▪◦\-–*]|(?:19|20)\d\d\b)')

    def __init__(self, config: AnchorConfig = DEFAULT_ANCHORS) -> None:
        self.config = config

    def parse(self, document: bytes) -> list[HomepageCitation]:
        text = pdf_text(document)
        citations = []
        for entry in self.entries(text):
            dois = _unique(match.group(0).rstrip(TRAILING_PUNCTUATION) for match in DOI_IN_TEXT.finditer(entry))
            arxiv = _unique(match.group(1) for match in ARXIV_IN_TEXT.finditer(entry))
            titles = _unique(title.strip(' .,;:') for title in QUOTED.findall(entry) if title_like(title, self.config))
            if dois or arxiv or titles:
                citations.append(HomepageCitation(entry, tuple(dois), tuple(arxiv), tuple(titles)))
        return citations

    def entries(self, text: str) -> list[str]:
        entries: list[list[str]] = [[]]
        for line in text.splitlines():
            if not line.strip() or self.ENTRY_START.match(line):
                entries.append([])
            if line.strip():
                entries[-1].append(line.strip())
        return [' '.join(lines) for lines in entries if lines]


def pdf_text(document: bytes) -> str:
    """Text of a PDF, page by page. Needs the optional ``pypdf`` dependency (the ``download`` extra). A damaged or
    truncated PDF (a CV cut at the page byte limit) is a ``ValueError``, like any other unreadable page."""
    try:
        from pypdf import PdfReader
        from pypdf.errors import PyPdfError
    except ImportError as exc:  # pragma: no cover - depends on the installed extras
        raise ValueError('Reading a PDF publication list needs pypdf (pip install ".[download]")') from exc
    try:
        return '\n'.join(page.extract_text() or '' for page in PdfReader(io.BytesIO(document)).pages)
    except PyPdfError as exc:
        raise ValueError(f'unreadable PDF: {exc}') from exc


class DocumentParser(Protocol):
    """Citations of one fetched document (an HTML page's text, or a PDF's bytes)."""

    def parse(self, document: Any) -> list[HomepageCitation]: ...


def is_pdf(content_type: str, body: bytes) -> bool:
    return 'pdf' in (content_type or '').lower() or body.startswith(b'%PDF')


class HomepageReader:
    """Turns a fetched publication page into citations: HTML pages and PDF CVs alike."""

    def __init__(self, html: CitationParser, pdf: DocumentParser) -> None:
        self.html, self.pdf = html, pdf

    def citations(self, content_type: str, body: bytes, decode: Callable[[], str]) -> list[HomepageCitation]:
        if is_pdf(content_type, body):
            return self.pdf.parse(body)
        return self.html.parse(decode())


YEAR = re.compile(r'\b(19[5-9]\d|20\d\d)\b')


class CitationRanker:
    """Orders citations by their value as anchors: naming the person (on a lab page those are theirs), recent
    (inside ``anchor_recent_years``: current affiliations, current author IDs), then research papers over talks,
    posters or datasets. Ties keep page order."""

    def __init__(self, config: AnchorConfig = DEFAULT_ANCHORS, current_year: int | None = None) -> None:
        self.config = config
        self.current_year = current_year or date.today().year
        self.markers = tuple(normalize(marker) for marker in config.non_paper_markers)

    def value(self, citation: HomepageCitation, name: str) -> tuple[bool, bool, bool]:
        text = normalize(citation.text)
        years = [int(year) for year in YEAR.findall(citation.text)]
        recent = any(self.current_year - year < self.config.anchor_recent_years for year in years)
        paper = not any(f' {marker} ' in f' {text} ' for marker in self.markers)
        return mentions(name, citation.text), recent, paper

    def rank(self, citations: Iterable[HomepageCitation], name: str) -> list[HomepageCitation]:
        return sorted(citations, key=lambda citation: tuple(not flag for flag in self.value(citation, name)))


def prioritise(citations: Iterable[HomepageCitation], name: str) -> list[HomepageCitation]:
    """Rank with the default configuration (see CitationRanker)."""
    return CitationRanker().rank(citations, name)


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))

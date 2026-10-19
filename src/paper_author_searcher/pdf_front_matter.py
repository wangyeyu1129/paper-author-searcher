"""Recover typographic text without fuzzy title or weaker author thresholds."""

import re
import unicodedata
from collections.abc import Sequence

from .matching import normalize
from .paper_authorship import byline_forms
from .paper_deduplication import author_signatures_match


def front_matter(text: str) -> str:
    # Keep bylines out of the abstract, main text and reference list.
    boundary = re.search(r'(?im)^\s*(?:abstract\b|(?:\d+\.?\s+)?introduction\b|references\b)', text)
    return text[: boundary.start()] if boundary else text


def clean_byline(text: str) -> str:
    text = unicodedata.normalize('NFKD', text)
    text = re.sub(r'(?<=[^\W\d_])\d+(?:\s*,\s*\d+)*', '', text)
    text = re.sub(r'[†‡∗*]', ' ', text)
    return normalize(text)


def authors_present(authors: Sequence[str], regions: Sequence[str], author_metadata: str) -> bool:
    if not authors:
        return False
    words = clean_byline(' '.join(front_matter(region) for region in regions) + ' ' + author_metadata).split()
    used: set[int] = set()
    for author in authors:
        match = _author_span(author, words, used)
        if match is None:
            return False
        used.update(range(*match))
    return True


def _author_span(author: str, words: Sequence[str], used: set[int]) -> tuple[int, int] | None:
    for form in byline_forms(author):
        length = len(normalize(form).split())
        if length < 2:
            continue
        for size in range(max(2, length - 1), length + 2):
            for start in range(len(words) - size + 1):
                end = start + size
                if used.intersection(range(start, end)):
                    continue
                observed = ' '.join(words[start:end])
                if author_signatures_match(form, observed):
                    return start, end
    return None


def title_in_region(title: str, region: str, compact: bool = True) -> bool:
    expected = normalize(title)
    if not expected:
        return False
    lines = [line.strip() for line in front_matter(region).splitlines() if line.strip()]
    # DVI extraction can insert spaces inside ligatures/words. Match the exact
    # character sequence, never an edit-distance approximation; bylines still
    # have to corroborate this alternative in the identity validator.
    for start in range(len(lines)):
        for end in range(start + 1, len(lines) + 1):
            observed = normalize(' '.join(lines[start:end]))
            if observed == expected:
                return True
            if compact and ''.join(observed.split()) == ''.join(expected.split()):
                return True
    return False


def metadata_title_kind(title: str, expected: str) -> str:
    title = title.strip()
    if not title or re.search(r'\.(?:dvi|tex|ps|eps|pdf|pptx?)\s*$', title, re.I):
        return 'technical'
    if normalize(title) == normalize(expected):
        return 'matching'
    # A series/volume prefix is bibliographic decoration, not another paper title.
    prefix = re.match(r'^[A-Z][A-Z0-9. ]*\s+\d+(?:\s*[,/]\s*\d+)*\s*[-:]\s*(.+)$', title)
    if prefix and normalize(prefix.group(1)) == normalize(expected):
        return 'decorated'
    return 'conflicting'


def publication_years(text: str) -> list[int]:
    patterns = (
        r'(?i)\b(?:published|copyright|©)\s*[:\s]*(\d{4})\b',
        r'(?i)\bproceedings\b[^\n]{0,180}\b((?:19|20)\d{2})\b',
    )
    years = set()
    for pattern in patterns:
        years.update(re.findall(pattern, text))
    return sorted(years)

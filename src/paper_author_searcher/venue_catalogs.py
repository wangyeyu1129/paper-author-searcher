"""Venue proceedings and institutional repositories as one local, indexed store.

Many CS papers exist bibliographically only in their venue's own proceedings (ACL Anthology, PMLR, CVF open
access), and many institutional repositories (OAI-PMH) hold copies nobody else indexes. Each ``CatalogSource``
reads its official bulk listing once and yields ``CatalogEntry``s:

- ``AclAnthologyCatalog``: the anthology's full BibTeX export;
- ``PmlrCatalog``: each configured volume's BibTeX;
- ``CvfCatalog``: each configured conference's all-papers page;
- ``OaiPmhRepository``: an institution's repository harvested with ``ListRecords`` (Dublin Core), page by page.

``CatalogIndexer`` writes them to a ``CatalogStore`` (SQLite) keyed by work key and by author name, so a lookup
is one indexed query: ``CatalogWorkLookup`` answers listed works, ``RepositoryNameSearch`` returns the entries of
the requested institution's repositories that name the person.
"""

import gzip
import io
import json
import logging
import re
import sqlite3
import threading
import unicodedata
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import IO, Any, Protocol

from .config import DEFAULT_MATCHING, CatalogConfig, MatchingConfig
from .homepage_citations import DOI_IN_TEXT
from .listed_works import AuthorMatcher, relabel_choice
from .logging_config import log_isolated_failure
from .matching import DOI_URL, best_similarity, canonical_doi, institution_similarity, normalize, work_keys
from .models import PaperRecord, PaperSourceResult, ProfessorProfile
from .paper_providers import parse_year

logger = logging.getLogger(__name__)

Fetch = Callable[[str], bytes]
GZIP_MAGIC = b'\x1f\x8b'

REPOSITORY_SOURCE = 'repository'
# Each repository is its own catalog, named by this prefix and its endpoint.
OAI_CATALOG_PREFIX = 'oai_pmh:'
OAI = '{http://www.openarchives.org/OAI/2.0/}'
DC = '{http://purl.org/dc/elements/1.1/}'
OAI_DC = '{http://www.openarchives.org/OAI/2.0/oai_dc/}'


# (name, endpoint, set); the set may be left out.
OAI_REPOSITORY_WITH_SET = 3


@dataclass(frozen=True)
class CatalogEntry:
    catalog: str
    key: str
    title: str
    authors: tuple[str, ...]
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    url: str | None = None
    pdf_url: str | None = None
    # The institution whose repository holds the entry (OAI-PMH only).
    institution: str | None = None

    def work_keys(self) -> set[str]:
        return work_keys([self.doi] if self.doi else [], [], [self.title] if self.title else [])


class CatalogSource(Protocol):
    name: str

    def entries(self) -> Iterator[CatalogEntry]: ...


# BibTeX ---------------------------------------------------------------------------------------------------------

ENTRY_START = re.compile(r'@(\w+)\s*\{\s*([^,\s]*)\s*,')
FIELD_NAME = re.compile(r'[\s,]*([\w-]+)\s*=\s*')
ACCENT = re.compile(r'\\([\'"`^~=.uvHc])\s*\{?\s*([A-Za-z])\s*\}?')
COMBINING = {
    "'": '\u0301',
    '"': '\u0308',
    '`': '\u0300',
    '^': '\u0302',
    '~': '\u0303',
    '=': '\u0304',
    '.': '\u0307',
    'u': '\u0306',
    'v': '\u030c',
    'H': '\u030b',
    'c': '\u0327',
}
COMMAND = re.compile(r'\\[A-Za-z]+\s*|\\(.)')
AUTHOR_SEPARATOR = re.compile(r'\s+and\s+')


def latex_text(value: str) -> str:
    """Plain text of a BibTeX value: accents combined, commands and braces dropped, whitespace collapsed."""
    text = ACCENT.sub(lambda match: match.group(2) + COMBINING[match.group(1)], value)
    text = COMMAND.sub(lambda match: match.group(1) or '', text)
    return unicodedata.normalize('NFC', ' '.join(text.replace('{', '').replace('}', '').split()))


def _field_value(text: str, start: int) -> tuple[str, int]:
    """A braced, quoted or bare value beginning at ``start``, and the position after it (one linear scan)."""
    if start >= len(text):
        return '', start
    opening = text[start]
    if opening not in '{"':
        end = start
        while end < len(text) and text[end] not in ',}\n':
            end += 1
        return text[start:end].strip(), end
    depth, position = 0, start
    while position < len(text):
        character = text[position]
        if character == '{':
            depth += 1
        elif character == '}':
            depth -= 1
            if opening == '{' and depth == 0:
                return text[start + 1 : position], position + 1
        elif character == '"' and opening == '"' and position > start and depth == 0:
            return text[start + 1 : position], position + 1
        position += 1
    return text[start + 1 :], position


def bibtex_entries(text: str) -> Iterator[tuple[str, str, dict[str, str]]]:
    """(entry type, citation key, lower-cased fields) of every entry, in order."""
    position = 0
    while match := ENTRY_START.search(text, position):
        kind, key, position = match.group(1).lower(), match.group(2), match.end()
        fields: dict[str, str] = {}
        while (name := FIELD_NAME.match(text, position)) is not None:
            value, position = _field_value(text, name.end())
            fields[name.group(1).lower()] = latex_text(value)
        yield kind, key, fields


def bibtex_stream(lines: Iterable[str]) -> Iterator[tuple[str, str, dict[str, str]]]:
    """``bibtex_entries`` over a line stream, one entry in memory at a time (an entry starts on a line that
    begins with ``@``), so a 100 MB export is never held as one string."""
    buffer: list[str] = []
    for line in lines:
        if line.startswith('@') and buffer:
            yield from bibtex_entries(''.join(buffer))
            buffer = []
        buffer.append(line)
    if buffer:
        yield from bibtex_entries(''.join(buffer))


def text_lines(body: bytes) -> Iterator[str]:
    """Decoded lines of a (possibly gzipped) download, decompressed and decoded incrementally."""
    raw: IO[bytes] = io.BytesIO(body)
    if body[:2] == GZIP_MAGIC:
        raw = gzip.GzipFile(fileobj=raw)  # type: ignore[assignment]  # a GzipFile is a binary file object
    yield from io.TextIOWrapper(raw, encoding='utf-8', errors='replace')


def bibtex_authors(value: str) -> tuple[str, ...]:
    """'Last, First and First Last' as display names in byline order."""
    names = []
    for part in AUTHOR_SEPARATOR.split(value.strip()):
        family, comma, given = part.partition(',')
        names.append(' '.join(f'{given} {family}'.split()) if comma else ' '.join(part.split()))
    return tuple(name for name in names if name)


def bibtex_entry(catalog: str, key: str, fields: dict[str, str], pdf_url: str | None = None) -> CatalogEntry:
    doi = DOI_IN_TEXT.search(fields.get('doi', ''))
    return CatalogEntry(
        catalog=catalog,
        key=key,
        title=fields.get('title', ''),
        authors=bibtex_authors(fields.get('author', '')),
        year=parse_year(fields.get('year')),
        venue=fields.get('booktitle') or fields.get('journal'),
        doi=doi.group(0) if doi else None,
        url=fields.get('url'),
        pdf_url=pdf_url or fields.get('pdf'),
    )


PAPER_TYPES = frozenset({'inproceedings', 'article', 'incollection'})


class AclAnthologyCatalog:
    name = 'acl_anthology'

    def __init__(self, fetch: Fetch, url: str) -> None:
        self.fetch, self.url = fetch, url

    def entries(self) -> Iterator[CatalogEntry]:
        for kind, key, fields in bibtex_stream(text_lines(self.fetch(self.url))):
            if kind in PAPER_TYPES and fields.get('author'):
                url = fields.get('url', '').rstrip('/')
                yield bibtex_entry(self.name, key, fields, url + '.pdf' if url else None)


class PmlrCatalog:
    name = 'pmlr'

    def __init__(self, fetch: Fetch, url_template: str, volumes: Sequence[int]) -> None:
        self.fetch, self.url_template, self.volumes = fetch, url_template, list(volumes)

    def entries(self) -> Iterator[CatalogEntry]:
        for volume in self.volumes:
            for kind, key, fields in bibtex_stream(text_lines(self.fetch(self.url_template.format(volume=volume)))):
                if kind in PAPER_TYPES and fields.get('author'):
                    yield bibtex_entry(self.name, key, fields)


# CVF ------------------------------------------------------------------------------------------------------------


class _CvfListing(HTMLParser):
    """``<dt class="ptitle"><a href=...>Title</a></dt><dd>`` author inputs and PDF links ``</dd>``, repeated."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.papers: list[dict[str, Any]] = []
        self.in_title = False

    def handle_starttag(self, tag: str, attrs: Sequence[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == 'dt' and 'ptitle' in (attributes.get('class') or '').split():
            self.papers.append({'title': '', 'url': None, 'authors': [], 'pdf': None})
            self.in_title = True
        elif not self.papers:
            return
        elif tag == 'a' and self.in_title:
            self.papers[-1]['url'] = attributes.get('href')
        elif tag == 'input' and attributes.get('name') == 'query_author' and attributes.get('value'):
            self.papers[-1]['authors'].append(attributes['value'])
        elif tag == 'a' and (attributes.get('href') or '').endswith('_paper.pdf'):
            self.papers[-1]['pdf'] = self.papers[-1]['pdf'] or attributes['href']

    def handle_endtag(self, tag: str) -> None:
        if tag == 'dt':
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title and self.papers:
            self.papers[-1]['title'] += data


class CvfCatalog:
    name = 'cvf'
    YEAR = re.compile(r'(\d{4})$')

    def __init__(self, fetch: Fetch, base_url: str, conferences: Sequence[str]) -> None:
        self.fetch, self.base_url, self.conferences = fetch, base_url.rstrip('/'), list(conferences)

    def entries(self) -> Iterator[CatalogEntry]:
        for conference in self.conferences:
            listing = _CvfListing()
            listing.feed(self.fetch(f'{self.base_url}/{conference}?day=all').decode('utf-8', 'replace'))
            listing.close()
            year = self.YEAR.search(conference)
            for paper in listing.papers:
                title = ' '.join(paper['title'].split())
                if not title or not paper['authors']:
                    continue
                yield CatalogEntry(
                    catalog=self.name,
                    key=paper['url'] or f'{conference}:{title}',
                    title=title,
                    authors=tuple(paper['authors']),
                    year=int(year.group(1)) if year else None,
                    venue=conference,
                    url=self._absolute(paper['url']),
                    pdf_url=self._absolute(paper['pdf']),
                )

    def _absolute(self, path: str | None) -> str | None:
        return None if not path else path if path.startswith('http') else self.base_url + '/' + path.lstrip('/')


# OAI-PMH --------------------------------------------------------------------------------------------------------


class OaiPmhRepository:
    """``ListRecords`` in Dublin Core, following resumption tokens up to ``maximum_pages``."""

    def __init__(
        self, client: Any, institution: str, endpoint: str, set_spec: str = '', since: str = '', maximum_pages: int = 1
    ) -> None:
        self.client, self.institution, self.endpoint = client, institution, endpoint
        self.set_spec, self.since, self.maximum_pages = set_spec, since, maximum_pages
        self.name = OAI_CATALOG_PREFIX + endpoint + (f'#{set_spec}' if set_spec else '')

    def entries(self) -> Iterator[CatalogEntry]:
        params: dict[str, Any] = {'verb': 'ListRecords', 'metadataPrefix': 'oai_dc'}
        params.update({name: value for name, value in (('set', self.set_spec), ('from', self.since)) if value})
        for _ in range(self.maximum_pages):
            root = self.client.get(self.endpoint, params, xml=True)
            error = root.find(OAI + 'error')
            if error is not None:
                if error.get('code') == 'noRecordsMatch':
                    return
                raise ValueError(f'OAI-PMH error {error.get("code")}: {error.text}')
            listing = root.find(OAI + 'ListRecords')
            if listing is None:
                return
            for record in listing.findall(OAI + 'record'):
                entry = self._entry(record)
                if entry is not None:
                    yield entry
            token = (listing.findtext(OAI + 'resumptionToken') or '').strip()
            if not token:
                return
            params = {'verb': 'ListRecords', 'resumptionToken': token}

    def _entry(self, record: ET.Element) -> CatalogEntry | None:
        header = record.find(OAI + 'header')
        if header is None or header.get('status') == 'deleted':
            return None
        metadata = record.find(f'{OAI}metadata/{OAI_DC}dc')
        if metadata is None:
            return None
        title = ' '.join((metadata.findtext(DC + 'title') or '').split())
        authors = tuple(' '.join((item.text or '').split()) for item in metadata.findall(DC + 'creator') if item.text)
        identifiers = [item.text or '' for item in metadata.findall(DC + 'identifier')]
        doi = next((match.group(0) for value in identifiers if (match := DOI_IN_TEXT.search(value))), None)
        url = next((value for value in identifiers if value.startswith('http')), None)
        if not title or not authors:
            return None
        return CatalogEntry(
            catalog=self.name,
            key=header.findtext(OAI + 'identifier') or url or title,
            title=title,
            authors=tuple(bibtex_authors(' and '.join(authors))),
            year=parse_year((metadata.findtext(DC + 'date') or '')[:4]),
            venue=metadata.findtext(DC + 'publisher'),
            doi=doi,
            url=url,
            institution=self.institution,
        )


# Store ----------------------------------------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries(id INTEGER PRIMARY KEY, catalog TEXT NOT NULL, entry TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS works(work TEXT NOT NULL, entry INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS authors(name_key TEXT NOT NULL, entry INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS works_by_work ON works(work);
CREATE INDEX IF NOT EXISTS authors_by_name ON authors(name_key);
CREATE INDEX IF NOT EXISTS entries_by_catalog ON entries(catalog);
"""


def author_key(name: str) -> str:
    """Name key independent of order and punctuation ("Lin, Mei" and "Mei Lin" agree)."""
    return ' '.join(sorted(normalize(name).split()))


class CatalogStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.executescript(SCHEMA)
        self._lock = threading.Lock()

    def replace(self, catalog: str, entries: Iterable[CatalogEntry]) -> int:
        """Replace one catalog's entries in a single transaction; returns how many were stored."""
        count = 0
        with self._lock, self._connection:
            old = 'SELECT id FROM entries WHERE catalog = ?'
            self._connection.execute(f'DELETE FROM works WHERE entry IN ({old})', (catalog,))
            self._connection.execute(f'DELETE FROM authors WHERE entry IN ({old})', (catalog,))
            self._connection.execute('DELETE FROM entries WHERE catalog = ?', (catalog,))
            for entry in entries:
                cursor = self._connection.execute(
                    'INSERT INTO entries(catalog, entry) VALUES (?, ?)', (catalog, json.dumps(asdict(entry)))
                )
                identifier = cursor.lastrowid
                self._connection.executemany(
                    'INSERT INTO works VALUES (?, ?)', [(work, identifier) for work in entry.work_keys()]
                )
                self._connection.executemany(
                    'INSERT INTO authors VALUES (?, ?)', [(author_key(name), identifier) for name in set(entry.authors)]
                )
                count += 1
        return count

    def _entries(self, sql: str, parameters: Sequence[Any]) -> list[CatalogEntry]:
        with self._lock:
            rows = self._connection.execute(sql, tuple(parameters)).fetchall()
        return [_entry(json.loads(text)) for (text,) in rows]

    def works(self, work: str) -> list[CatalogEntry]:
        return self._entries(
            'SELECT entries.entry FROM works JOIN entries ON entries.id = works.entry WHERE works.work = ? '
            'ORDER BY entries.id',
            (work,),
        )

    def by_author(self, name: str, catalog_prefix: str = '') -> list[CatalogEntry]:
        """Entries naming the person, optionally only from catalogs whose name starts with ``catalog_prefix``."""
        sql = 'SELECT entries.entry FROM authors JOIN entries ON entries.id = authors.entry WHERE authors.name_key = ?'
        parameters: list[Any] = [author_key(name)]
        if catalog_prefix:
            sql += ' AND substr(entries.catalog, 1, ?) = ?'
            parameters += [len(catalog_prefix), catalog_prefix]
        return self._entries(sql + ' ORDER BY entries.id', parameters)

    def counts(self) -> dict[str, int]:
        with self._lock:
            rows = self._connection.execute('SELECT catalog, COUNT(*) FROM entries GROUP BY catalog').fetchall()
        return dict(rows)


def _entry(data: dict[str, Any]) -> CatalogEntry:
    return CatalogEntry(**{**data, 'authors': tuple(data.get('authors') or ())})


@dataclass
class IndexReport:
    stored: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)


class CatalogIndexer:
    """Indexes each source into the store; a failing source keeps its previous entries and is reported."""

    def __init__(self, store: CatalogStore, sources: Sequence[CatalogSource]) -> None:
        self.store, self.sources = store, list(sources)

    def build(self) -> IndexReport:
        report = IndexReport()
        for source in self.sources:
            try:
                # Streamed: replace() runs in one transaction, so a source failing midway keeps its previous entries.
                report.stored[source.name] = self.store.replace(source.name, source.entries())
            except Exception as exc:
                log_isolated_failure(logger, 'Catalog %s failed: %s', source.name, exc)
                report.errors[source.name] = str(exc)
        return report


# Lookups --------------------------------------------------------------------------------------------------------


def catalog_record(
    entry: CatalogEntry, choose: Callable[[list[dict[str, Any]]], str | None], relabel: str | None = None
) -> PaperRecord | None:
    source = REPOSITORY_SOURCE if entry.institution else entry.catalog
    details: list[dict[str, Any]] = [
        {'name': name, 'author_id': f'{source}:{entry.key}#{index}', 'role': 'author'}
        for index, name in enumerate(entry.authors)
    ]
    author_id = relabel_choice(details, choose(details), relabel)
    if not author_id:
        return None
    doi = canonical_doi(entry.doi).removeprefix('doi:') if entry.doi else None
    return PaperRecord(
        title=entry.title,
        authors=list(entry.authors),
        year=entry.year,
        doi=doi,
        venue=entry.venue,
        abstract=None,
        source=source,
        source_paper_id=entry.key,
        source_author_id=author_id,
        paper_url=entry.url or (DOI_URL + doi if doi else None),
        publication_type=None,
        author_details=details,
        target_author_present=True,
        external_ids={'pdf_url': entry.pdf_url} if entry.pdf_url else {},
        raw_metadata={'catalog': asdict(entry)},
    )


class CatalogWorkLookup:
    """A listed work in the venue catalogs and repositories, by DOI or exact title."""

    name = 'catalogs'

    def __init__(self, store: CatalogStore, authors: AuthorMatcher) -> None:
        self.store, self.authors = store, authors

    def find(self, key: str, profile: ProfessorProfile) -> PaperRecord | None:
        for entry in self.store.works(key):
            record = catalog_record(entry, lambda details: self.authors.pick(profile, details))
            if record is not None:
                return record
        return None


class RepositoryNameSearch:
    """Entries of the requested institution's own repositories that name the person."""

    name = REPOSITORY_SOURCE

    def __init__(self, store: CatalogStore, matching: MatchingConfig = DEFAULT_MATCHING) -> None:
        self.store, self.matching = store, matching
        self.authors = AuthorMatcher(matching)

    def author_key(self, profile: ProfessorProfile) -> str:
        return 'name:' + normalize(profile.requested_name or profile.name)

    def collect(self, profile: ProfessorProfile) -> PaperSourceResult:
        key = self.author_key(profile)
        result = PaperSourceResult(source=self.name, author_id=key, pagination_complete=True, complete=True)
        school = profile.requested_university or profile.university or ''
        for entry in self.store.by_author(profile.requested_name or profile.name, OAI_CATALOG_PREFIX):
            result.records_received += 1
            similarity = best_similarity(
                [school], [entry.institution or ''], lambda a, b: institution_similarity(a, b, self.matching)
            )
            if similarity < self.matching.matched_school:
                continue
            record = catalog_record(entry, lambda details: self.authors.pick(profile, details), key)
            if record is not None:
                record.attribution_evidence.append(f"Listed in {entry.institution}'s repository; no author ID")
                result.papers.append(record)
        return result


def catalog_sources(config: CatalogConfig, fetch: Fetch, client: Any) -> list[CatalogSource]:
    """The configured sources, in order (repositories expand to one source each)."""
    builders: dict[str, Callable[[], list[CatalogSource]]] = {
        'acl_anthology': lambda: [AclAnthologyCatalog(fetch, config.acl_anthology_url)],
        'pmlr': lambda: [PmlrCatalog(fetch, config.pmlr_bibliography_url, config.pmlr_volumes)],
        'cvf': lambda: [CvfCatalog(fetch, config.cvf_url, config.cvf_conferences)],
        'oai_pmh': lambda: [
            OaiPmhRepository(
                client,
                repository[0],
                repository[1],
                repository[2] if len(repository) >= OAI_REPOSITORY_WITH_SET else '',
                config.oai_from,
                config.oai_maximum_pages,
            )
            for repository in config.oai_repositories
        ],
    }
    unknown = sorted(set(config.sources) - set(builders))
    if unknown:
        raise ValueError(f'Unknown catalog sources {unknown}; available: {sorted(builders)}')
    return [source for name in config.sources for source in builders[name]()]


def probe_fetch(client: Any, maximum_bytes: int) -> Fetch:
    """Whole-body download through the shared client's bounded probe; a truncated body is an error."""

    def fetch(url: str) -> bytes:
        response = client.probe(url, maximum_bytes, maximum_bytes)
        if not response.body_complete:
            raise ValueError(f'{url} exceeds {maximum_bytes} bytes')
        return bytes(response.prefix)

    return fetch

"""DBLP from its official data dump instead of the API.

The DBLP API answers automated clients with an anti-bot page, and DBLP asks bulk users to work from the dump
(https://dblp.org/xml/dblp.xml.gz). ``DblpDumpIndexer`` streams the dump once into an SQLite index; the rest of
this module serves that index:

- ``DblpDumpClient`` answers the two API endpoints the DBLP providers call (author search and PID bibliography)
  with documents of the API's own shape, so ``DBLPProvider`` and ``DBLPPaperProvider`` run unchanged on it;
- ``DblpWorkLookup`` finds a listed work by DOI, arXiv ID or exact title (``matching.work_keys``).

DBLP disambiguates homonyms with a four-digit suffix ("Wei Wang 0001"); each person page (a ``www`` element keyed
``homepages/<pid>``) lists every name of that person, which is how names map to PIDs.
"""

import gzip
import hashlib
import re
import sqlite3
import threading
import xml.etree.ElementTree as ET
import zlib
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from html.entities import name2codepoint
from pathlib import Path
from typing import IO, Any
from urllib.parse import urlsplit

from .config import DEFAULT_DBLP_DUMP, DEFAULT_SOURCES, DblpDumpConfig, SourceConfig
from .dblp_format import ARXIV_LINK, base_name
from .http import SourceError
from .listed_works import AuthorMatcher
from .matching import canonical_arxiv, normalize, work_keys
from .models import PaperRecord, ProfessorProfile
from .paper_providers import dblp_record

HOMEPAGES = 'homepages/'
# SQLite allows 999 bound parameters per statement in older builds.
PARAMETER_CHUNK = 900

# Version 2: XML stored zlib-compressed; authorship and work keys stored as 64-bit hashes referencing records by
# row ID (10.9 GB -> about 4 GB for the full dump). Every read re-checks what a hash returned, so a collision can
# never return a record that does not carry the requested name or work.
SCHEMA_VERSION = '2'
SCHEMA = """
CREATE TABLE meta(name TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE names(name TEXT PRIMARY KEY, pid TEXT NOT NULL, name_key TEXT NOT NULL, name_hash INTEGER NOT NULL);
CREATE TABLE persons(pid TEXT PRIMARY KEY, xml BLOB NOT NULL);
CREATE TABLE records(id INTEGER PRIMARY KEY, key TEXT NOT NULL UNIQUE, xml BLOB NOT NULL);
CREATE TABLE authorship(name_hash INTEGER NOT NULL, record INTEGER NOT NULL);
CREATE TABLE works(work_hash INTEGER NOT NULL, record INTEGER NOT NULL);
"""
INDEXES = """
CREATE INDEX names_by_key ON names(name_key);
CREATE INDEX names_by_pid ON names(pid);
CREATE INDEX authorship_by_name ON authorship(name_hash);
CREATE INDEX works_by_work ON works(work_hash);
"""
PEOPLE_TAGS = ('author', 'editor')


def digest(text: str) -> int:
    """A signed 64-bit hash (SQLite's INTEGER range) of a name or work key."""
    return int.from_bytes(hashlib.blake2b(text.encode('utf-8'), digest_size=8).digest(), 'big', signed=True)


def name_key(name: str) -> str:
    return normalize(base_name(name))


def record_work_keys(element: ET.Element) -> set[str]:
    links = [item.text or '' for item in element.findall('ee')]
    dois = [link for link in links if 'doi.org/' in link]
    arxiv = [canonical_arxiv(match.group(1)) or '' for link in links for match in ARXIV_LINK.finditer(link)]
    heading = element.find('title')
    title = ''.join(heading.itertext()) if heading is not None else ''
    return work_keys(dois, [value for value in arxiv if value], [title.rstrip('.')] if title else [])


@dataclass
class IndexStatistics:
    persons: int = 0
    names: int = 0
    records: int = 0
    skipped: dict[str, int] = field(default_factory=dict)


def _parser() -> ET.XMLParser:
    """The dump declares its character entities in an external DTD; they are the HTML ones."""
    parser = ET.XMLParser()
    parser.entity.update({name: chr(codepoint) for name, codepoint in name2codepoint.items()})
    return parser


def _open(path: Path) -> IO[bytes]:
    if path.suffix == '.gz':
        return gzip.GzipFile(path, 'rb')  # type: ignore[return-value]  # a GzipFile is a binary file object
    return path.open('rb')


class DblpDumpIndexer:
    """Streams the dump (constant memory: each top-level element is cleared once stored) into a new index file,
    written beside the target and moved into place only when complete."""

    def __init__(self, config: DblpDumpConfig = DEFAULT_DBLP_DUMP) -> None:
        self.config = config
        self.record_tags = frozenset(config.record_tags)
        self.kept = frozenset(config.kept_fields)

    def build(self, dump: Path, index: Path) -> IndexStatistics:
        index.parent.mkdir(parents=True, exist_ok=True)
        partial = index.with_name(index.name + '.partial')
        partial.unlink(missing_ok=True)
        statistics = IndexStatistics()
        connection = sqlite3.connect(partial)
        try:
            connection.executescript('PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;' + SCHEMA)
            connection.execute("INSERT INTO meta VALUES ('schema', ?)", (SCHEMA_VERSION,))
            batches: dict[str, list[tuple]] = {table: [] for table in STATEMENTS}
            with _open(dump) as source:
                for element in self._elements(source):
                    self._add(element, batches, statistics)
                    if sum(map(len, batches.values())) >= self.config.batch_size:
                        self._flush(connection, batches)
            self._flush(connection, batches)
            connection.executescript(INDEXES)
            connection.commit()
        finally:
            connection.close()
        partial.replace(index)
        return statistics

    def _elements(self, source: IO[bytes]) -> Iterator[ET.Element]:
        root: ET.Element | None = None
        depth = 0
        for event, element in ET.iterparse(source, events=('start', 'end'), parser=_parser()):
            if event == 'start':
                if root is None:
                    root = element
                depth += 1
                continue
            depth -= 1
            if depth == 1 and root is not None:
                yield element
                root.clear()

    def _add(self, element: ET.Element, batches: dict[str, list[tuple]], statistics: IndexStatistics) -> None:
        key = element.get('key') or ''
        if element.tag == 'www' and key.startswith(HOMEPAGES) and element.get('publtype') != 'disambiguation':
            pid = key[len(HOMEPAGES) :]
            names = [item.text for item in element.findall('author') if item.text]
            element.tag = 'person'
            batches['persons'].append((pid, self._pack(element)))
            batches['names'].extend((name, pid, name_key(name), digest(name)) for name in names)
            statistics.persons += 1
            statistics.names += len(names)
            return
        if element.tag not in self.record_tags or not key:
            statistics.skipped[element.tag] = statistics.skipped.get(element.tag, 0) + 1
            return
        for child in list(element):
            if child.tag not in self.kept:
                element.remove(child)
            child.tail = None
        statistics.records += 1
        record = statistics.records  # row IDs are assigned here, so references need no lookup while building
        batches['records'].append((record, key, self._pack(element)))
        people = {item.text for item in element if item.tag in PEOPLE_TAGS and item.text}
        batches['authorship'].extend((digest(name), record) for name in people)
        batches['works'].extend((digest(work), record) for work in record_work_keys(element))

    def _pack(self, element: ET.Element) -> bytes:
        return zlib.compress(ET.tostring(element, encoding='utf-8'), self.config.compression_level)

    @staticmethod
    def _flush(connection: sqlite3.Connection, batches: dict[str, list[tuple]]) -> None:
        for table, rows in batches.items():
            if rows:
                connection.executemany(STATEMENTS[table], rows)
                rows.clear()
        connection.commit()


STATEMENTS = {
    'names': 'INSERT OR IGNORE INTO names VALUES (?, ?, ?, ?)',
    'persons': 'INSERT OR REPLACE INTO persons VALUES (?, ?)',
    'records': 'INSERT INTO records VALUES (?, ?, ?)',
    'authorship': 'INSERT INTO authorship VALUES (?, ?)',
    'works': 'INSERT INTO works VALUES (?, ?)',
}


def _unpack(blob: bytes) -> ET.Element:
    return ET.fromstring(zlib.decompress(blob))


def _chunks(values: Sequence[Any]) -> Iterator[Sequence[Any]]:
    for start in range(0, len(values), PARAMETER_CHUNK):
        yield values[start : start + PARAMETER_CHUNK]


class DblpDumpIndex:
    """Read-only queries over a built index; safe to share between threads."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if not self.path.is_file():
            raise FileNotFoundError(f'DBLP dump index not found: {self.path}')
        self._connection = sqlite3.connect(f'file:{self.path}?mode=ro', uri=True, check_same_thread=False)
        self._lock = threading.Lock()
        try:
            version = self._rows("SELECT value FROM meta WHERE name = 'schema'")
        except sqlite3.DatabaseError:
            version = []
        if not version or version[0][0] != SCHEMA_VERSION:
            found = version[0][0] if version else 'unversioned'
            raise ValueError(f'DBLP dump index {self.path} has schema {found}, not {SCHEMA_VERSION}: rebuild it')

    def _rows(self, sql: str, parameters: Iterable[Any] = ()) -> list[tuple]:
        with self._lock:
            return self._connection.execute(sql, tuple(parameters)).fetchall()

    def _in(self, sql: str, values: Sequence[Any]) -> list[tuple]:
        rows: list[tuple] = []
        for chunk in _chunks(list(values)):
            rows += self._rows(sql.format(', '.join('?' * len(chunk))), chunk)
        return rows

    def search(self, name: str, limit: int) -> list[tuple[str, str]]:
        """(pid, printed name) of the people with this name, those with most publications first."""
        rows = self._rows(
            'SELECT names.pid, MIN(names.name), COUNT(authorship.record) AS papers FROM names '
            'LEFT JOIN authorship ON authorship.name_hash = names.name_hash WHERE names.name_key = ? '
            'GROUP BY names.pid ORDER BY papers DESC, names.pid LIMIT ?',
            (name_key(name), limit),
        )
        return [(pid, printed) for pid, printed, _ in rows]

    def bibliography(self, pid: str) -> ET.Element | None:
        """The person's page and publications, in the shape of the API's ``/pid/<pid>.xml``."""
        person = self._rows('SELECT xml FROM persons WHERE pid = ?', (pid,))
        if not person:
            return None
        names = [name for (name,) in self._rows('SELECT name FROM names WHERE pid = ?', (pid,))]
        hashes = [digest(name) for name in names]
        ids = {record for (record,) in self._in('SELECT record FROM authorship WHERE name_hash IN ({})', hashes)}
        wanted = set(names)
        records = [
            element
            for element in self._records(ids)
            if any(item.text in wanted for item in element if item.tag in PEOPLE_TAGS)
        ]
        root = ET.Element('dblpperson', {'name': names[0] if names else '', 'pid': pid, 'n': str(len(records))})
        root.append(_unpack(person[0][0]))
        for record in records:
            ET.SubElement(root, 'r').append(record)
        return root

    def works(self, work: str) -> list[ET.Element]:
        ids = {record for (record,) in self._rows('SELECT record FROM works WHERE work_hash = ?', (digest(work),))}
        return [element for element in self._records(ids) if work in record_work_keys(element)]

    def _records(self, ids: Iterable[int]) -> list[ET.Element]:
        """Records in key order, every author and editor carrying the PID their printed name belongs to."""
        rows = sorted(self._in('SELECT key, xml FROM records WHERE id IN ({})', sorted(ids)))
        elements = [_unpack(blob) for _, blob in rows]
        people = sorted(
            {item.text for element in elements for item in element if item.tag in ('author', 'editor') and item.text}
        )
        pids = dict(self._in('SELECT name, pid FROM names WHERE name IN ({})', people))
        for element in elements:
            for item in element:
                if item.tag in ('author', 'editor') and item.text in pids:
                    item.set('pid', pids[item.text])
        return elements


class DblpDumpClient:
    """Serves the DBLP API endpoints the providers use from the dump index; anything else is a 404."""

    PID_PATH = re.compile(r'/pid/(?P<pid>.+)\.xml$')

    def __init__(self, index: DblpDumpIndex, source_config: SourceConfig = DEFAULT_SOURCES) -> None:
        self.index = index
        self.base_url = source_config.dblp_url.rstrip('/')

    def get(self, url: str, params: dict | None = None, headers: dict | None = None, xml: bool = False) -> Any:
        path = urlsplit(url).path
        if path.endswith('/search/author/api'):
            params = params or {}
            people = self.index.search(str(params.get('q') or ''), int(params.get('h') or 30))
            hits = [{'info': {'author': name, 'url': f'{self.base_url}/pid/{pid}'}} for pid, name in people]
            return {'result': {'hits': {'@total': str(len(hits)), 'hit': hits}}}
        match = self.PID_PATH.search(path)
        if match:
            root = self.index.bibliography(match.group('pid'))
            if root is None:
                raise SourceError(f'{url} HTTP 404 (not in the DBLP dump index)', status_code=404)
            return root
        raise SourceError(f'{url} is not served by the DBLP dump index', status_code=404)


class DblpWorkLookup:
    """A listed work in the DBLP dump, by DOI, arXiv ID or exact title, as a record of the person's PID."""

    name = 'dblp'

    def __init__(self, index: DblpDumpIndex, authors: AuthorMatcher, source_config: SourceConfig = DEFAULT_SOURCES):
        self.index, self.authors = index, authors
        self.base_url = source_config.dblp_url.rstrip('/')

    def find(self, key: str, profile: ProfessorProfile) -> PaperRecord | None:
        for element in self.index.works(key):
            pid = self.authors.single(
                profile,
                (
                    (item.get('pid') or '', [base_name(item.text or '')])
                    for item in element.findall('author')
                    if item.get('pid')
                ),
            )
            if pid:
                return dblp_record(element, pid, self.base_url)
        return None

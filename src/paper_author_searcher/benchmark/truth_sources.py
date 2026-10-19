"""Parse independent, researcher-maintained bibliographies into neutral truth records.

Every source reads a local snapshot so truth can be rebuilt offline and audited; live fetching
lives in ``snapshots.py``. None of these sources reads output of the search pipeline.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol

from .roster import BenchmarkPerson, TruthSourceSpec

ARXIV_ID = re.compile(
    r'(?:arxiv\.org/(?:abs|pdf)/|arxiv:\s*)?(\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?', re.IGNORECASE
)
DOI_PATTERN = re.compile(r'10\.\d{4,9}/\S+', re.IGNORECASE)
YEAR = re.compile(r'(19|20)\d{2}')


@dataclass
class TruthRecord:
    title: str
    year: Optional[int]
    authors: List[str]
    source_kind: str
    evidence: str
    doi: Optional[str] = None
    external_ids: Dict[str, str] = field(default_factory=dict)
    venue: Optional[str] = None
    work_type: Optional[str] = None
    related_dois: List[str] = field(default_factory=list)


class TruthSource(Protocol):
    kind: str

    def load(self, person: BenchmarkPerson, spec: TruthSourceSpec, snapshot_dir: Path) -> List[TruthRecord]: ...


class TruthSourceRegistry:
    """Open for new bibliography formats without editing the builder."""

    def __init__(self) -> None:
        self._sources: Dict[str, TruthSource] = {}

    def register(self, source: TruthSource) -> 'TruthSourceRegistry':
        if source.kind in self._sources:
            raise ValueError('Truth source already registered: ' + source.kind)
        self._sources[source.kind] = source
        return self

    def get(self, kind: str) -> TruthSource:
        try:
            return self._sources[kind]
        except KeyError:
            raise ValueError('Unknown truth source kind: ' + kind) from None

    def kinds(self) -> List[str]:
        return sorted(self._sources)


def clean_doi(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    match = DOI_PATTERN.search(value.strip())
    return match.group(0).rstrip('.,;)').lower() if match else None


def clean_arxiv(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    match = ARXIV_ID.search(value.strip())
    return match.group(1) if match else None


VANCOUVER_NAME = re.compile(r'^(?P<family>[^\W\d_][\w\'\- ]*?)\s+(?P<initials>[A-Z]{1,3})$')


def vancouver_name(name: str) -> str:
    """PubMed bylines write "Trinidad MI"; return "M. I. Trinidad" so initials-aware matching applies."""
    match = VANCOUVER_NAME.match(name.strip())
    if not match:
        return name.strip()
    return ' '.join([letter + '.' for letter in match.group('initials')] + [match.group('family')])


def parse_year(value) -> Optional[int]:
    if value is None:
        return None
    match = YEAR.search(str(value))
    return int(match.group(0)) if match else None


def snapshot_path(snapshot_dir: Path, locator: str) -> Path:
    path = (snapshot_dir / locator).resolve()
    if snapshot_dir.resolve() not in path.parents:
        raise ValueError('Snapshot locator must stay inside the person snapshot directory')
    return path


def orcid_snapshot_name(orcid: str) -> str:
    return f'orcid-{orcid}.json'


ORCID_TYPES = {
    'journal-article': 'journal_article',
    'conference-paper': 'conference_paper',
    'preprint': 'preprint',
    'working-paper': 'working_paper',
    'book-chapter': 'book_chapter',
    'review': 'review',
    'book': 'book',
    'edited-book': 'book',
    'dissertation-thesis': 'thesis',
    'dissertation': 'thesis',
    'data-set': 'dataset',
    'software': 'software',
    'conference-poster': 'poster',
    'conference-abstract': 'abstract',
    'lecture-speech': 'slides',
    'report': 'report',
    'supervised-student-publication': 'other',
    'other': 'other',
}
ORCID_IDENTIFIER_RELATIONSHIPS = ('self', 'version-of')


def _value(node, *keys):
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


class OrcidSource:
    """ORCID works curated by (or with permission of) the researcher."""

    kind = 'orcid'

    def load(self, person, spec, snapshot_dir):
        path = snapshot_path(snapshot_dir, orcid_snapshot_name(spec.locator))
        with path.open(encoding='utf-8') as handle:
            snapshot = json.load(handle)
        records = []
        for work in snapshot.get('works') or []:
            title = _value(work, 'title', 'title', 'value')
            if not title:
                continue
            doi: Optional[str] = None
            related: List[str] = []
            external: Dict[str, str] = {}
            for identifier in _value(work, 'external-ids', 'external-id') or []:
                if identifier.get('external-id-relationship') not in ORCID_IDENTIFIER_RELATIONSHIPS:
                    continue
                kind = (identifier.get('external-id-type') or '').lower()
                value = identifier.get('external-id-value') or ''
                cleaned_doi = clean_doi(value) if kind == 'doi' else None
                cleaned_arxiv = clean_arxiv(value) if kind == 'arxiv' else None
                if cleaned_doi:
                    if identifier.get('external-id-relationship') == 'self' and doi is None:
                        doi = cleaned_doi
                    else:
                        related.append(cleaned_doi)
                elif cleaned_arxiv:
                    external['arxiv'] = cleaned_arxiv
                elif kind == 'pmid' and value.strip().isdigit():
                    external.update(pubmed_identifiers(value.strip()))
            contributors = _value(work, 'contributors', 'contributor') or []
            authors = [_value(contributor, 'credit-name', 'value') for contributor in contributors]
            records.append(
                TruthRecord(
                    title=title.strip(),
                    year=parse_year(_value(work, 'publication-date', 'year', 'value')),
                    authors=[name.strip() for name in authors if name and name.strip()],
                    source_kind=self.kind,
                    doi=doi,
                    external_ids=external,
                    related_dois=related,
                    venue=_value(work, 'journal-title', 'value'),
                    work_type=ORCID_TYPES.get(work.get('type')),
                    evidence='ORCID {} put-code {}'.format(spec.locator, work.get('put-code')),
                )
            )
        return records


def pubmed_identifiers(pmid: str) -> Dict[str, str]:
    # Semantic Scholar stores the bare PMID; OpenAlex stores the PubMed URL.
    return {'pubmed': pmid, 'pmid': 'https://pubmed.ncbi.nlm.nih.gov/' + pmid}


BIBTEX_TYPES = {
    'article': 'journal_article',
    'inproceedings': 'conference_paper',
    'conference': 'conference_paper',
    'incollection': 'book_chapter',
    'inbook': 'book_chapter',
    'book': 'book',
    'phdthesis': 'thesis',
    'mastersthesis': 'thesis',
    'techreport': 'report',
}
PREPRINT_VENUE = re.compile(r'arxiv|biorxiv|medrxiv|ssrn|preprint|working paper', re.IGNORECASE)
LATEX_ACCENT = re.compile(r'\\[\'"`^~=.uvHckb]\s*\{?([A-Za-z])\}?')
LATEX_COMMAND = re.compile(r'\\[A-Za-z]+\s*')


def latex_text(value: str) -> str:
    value = LATEX_ACCENT.sub(r'\1', value)
    value = LATEX_COMMAND.sub('', value)
    return ' '.join(value.replace('{', '').replace('}', '').replace('~', ' ').split())


def bibtex_entries(text: str) -> List[Dict[str, str]]:
    """Small brace-aware parser; enough for author-published .bib files, no third-party dependency."""
    entries: List[Any] = []
    position = 0
    while True:
        start = text.find('@', position)
        if start < 0:
            return entries
        brace = text.find('{', start)
        if brace < 0:
            return entries
        entry_type = text[start + 1 : brace].strip().lower()
        depth, index = 0, brace
        while index < len(text):
            if text[index] == '{':
                depth += 1
            elif text[index] == '}':
                depth -= 1
                if depth == 0:
                    break
            index += 1
        body = text[brace + 1 : index]
        position = index + 1
        if entry_type in ('comment', 'preamble', 'string'):
            continue
        fields = {'ENTRYTYPE': entry_type}
        _key, _, rest = body.partition(',')
        cursor = 0
        while True:
            match = FIELD_NAME.match(rest, cursor)
            if not match:
                break
            value, cursor = _bibtex_value(rest, match.end())
            fields[match.group(1).lower()] = value
        entries.append(fields)


FIELD_NAME = re.compile(r'[\s,]*([A-Za-z_\-]+)\s*=\s*')


def _bibtex_value(text: str, cursor: int):
    """Return one field value and the index after it: braced, quoted or bare (numbers, macros)."""
    if cursor >= len(text):
        return '', cursor
    if text[cursor] == '{':
        depth = 0
        for end in range(cursor, len(text)):
            depth += {'{': 1, '}': -1}.get(text[end], 0)
            if depth == 0:
                return text[cursor + 1 : end], end + 1
        return text[cursor + 1 :], len(text)
    if text[cursor] == '"':
        end = text.find('"', cursor + 1)
        end = len(text) if end < 0 else end
        return text[cursor + 1 : end], end + 1
    end = text.find(',', cursor)
    end = len(text) if end < 0 else end
    return text[cursor:end].strip(), end


class BibtexSource:
    """BibTeX exported by the researcher from a homepage, CV or lab site."""

    kind = 'bibtex'

    def load(self, person, spec, snapshot_dir):
        text = snapshot_path(snapshot_dir, spec.locator).read_text(encoding='utf-8')
        records = []
        for entry in bibtex_entries(text):
            title = latex_text(entry.get('title', ''))
            if not title:
                continue
            venue = latex_text(entry.get('journal') or entry.get('booktitle') or entry.get('publisher') or '') or None
            arxiv = None
            if (entry.get('archiveprefix') or entry.get('eprinttype') or '').lower() == 'arxiv' or 'arxiv' in (
                venue or ''
            ).lower():
                arxiv = clean_arxiv(entry.get('eprint') or entry.get('journal') or entry.get('url') or '')
            url = entry.get('url', '')
            if arxiv is None and 'arxiv' in url.lower():
                arxiv = clean_arxiv(url)
            work_type = BIBTEX_TYPES.get(entry['ENTRYTYPE'])
            if (venue and PREPRINT_VENUE.search(venue)) or (work_type is None and arxiv):
                work_type = 'preprint'
            authors = [latex_text(name) for name in re.split(r'\s+and\s+', entry.get('author', '')) if name.strip()]
            records.append(
                TruthRecord(
                    title=title,
                    year=parse_year(entry.get('year')),
                    authors=authors,
                    source_kind=self.kind,
                    doi=clean_doi(entry.get('doi')),
                    external_ids={'arxiv': arxiv} if arxiv else {},
                    venue=venue,
                    work_type=work_type,
                    evidence=f'BibTeX {spec.locator} ({spec.url})',
                )
            )
        return records


class ManualListSource:
    """Entries transcribed from a public publication page, with page URL and capture date."""

    kind = 'manual'

    def load(self, person, spec, snapshot_dir):
        with snapshot_path(snapshot_dir, spec.locator).open(encoding='utf-8') as handle:
            snapshot = json.load(handle)
        origin = snapshot.get('source_url') or spec.url
        records = []
        for index, entry in enumerate(snapshot.get('entries') or []):
            if not entry.get('title'):
                continue
            external = {}
            arxiv = clean_arxiv(entry.get('arxiv') or '') or (
                clean_arxiv(entry.get('url', '')) if 'arxiv' in entry.get('url', '').lower() else None
            )
            if arxiv:
                external['arxiv'] = arxiv
            if entry.get('pmid'):
                external.update(pubmed_identifiers(str(entry['pmid'])))
            records.append(
                TruthRecord(
                    title=entry['title'].strip(),
                    year=parse_year(entry.get('year')),
                    authors=[vancouver_name(name) for name in entry.get('authors') or [] if name.strip()],
                    source_kind=self.kind,
                    doi=clean_doi(entry.get('doi')),
                    external_ids=external,
                    venue=entry.get('venue'),
                    work_type=entry.get('type'),
                    evidence='Public list {} entry {} captured {}'.format(
                        origin, index + 1, snapshot.get('captured_at')
                    ),
                )
            )
        return records


def default_registry() -> TruthSourceRegistry:
    return TruthSourceRegistry().register(OrcidSource()).register(BibtexSource()).register(ManualListSource())

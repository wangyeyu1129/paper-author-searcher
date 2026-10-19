"""Live capture of truth inputs into dated local snapshots; building truth afterwards is offline."""

import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from paper_author_searcher.http import HttpClient, SourceError
from paper_author_searcher.jsonio import read_json, write_json
from paper_author_searcher.matching import normalize

from .roster import BenchmarkPerson
from .settings import BenchmarkSettings
from .truth_sources import TruthSourceRegistry, orcid_snapshot_name

CROSSREF_SNAPSHOT = 'crossref-lookups.json'
ARXIV_SNAPSHOT = 'arxiv-lookups.json'
ATOM = '{http://www.w3.org/2005/Atom}'


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def title_key(title: str) -> str:
    return normalize(title)


def _cached(snapshot: dict, key: str) -> bool:
    """Successful lookups are reused; failed ones are retried on the next fetch."""
    return key in snapshot['lookups'] and 'error' not in snapshot['lookups'][key]


class OrcidFetcher:
    def __init__(self, settings: BenchmarkSettings, client: HttpClient) -> None:
        self.settings = settings.sources
        self.client = client

    def fetch(self, orcid: str, destination: Path) -> dict[str, object]:
        base = '{}/{}'.format(self.settings.orcid_api_url.rstrip('/'), orcid)
        summary = self.client.get(base + '/works')
        codes = []
        for group in summary.get('group') or []:
            # Each group is one work as merged by ORCID; the first summary is ORCID's preferred version.
            summaries = group.get('work-summary') or []
            if summaries:
                codes.append(str(summaries[0]['put-code']))
        works, errors = [], []
        size = self.settings.orcid_bulk_size
        for start in range(0, len(codes), size):
            batch = codes[start : start + size]
            response = self.client.get(base + '/works/' + ','.join(batch))
            for item in response.get('bulk') or []:
                if item.get('work'):
                    works.append(item['work'])
                else:
                    errors.append(item.get('error') or item)
        snapshot = {
            'orcid': orcid,
            'fetched_at': _now(),
            'api_url': base,
            'work_groups': len(codes),
            'works': works,
            'errors': errors,
        }
        write_json(destination / orcid_snapshot_name(orcid), snapshot)
        return {'orcid': orcid, 'works': len(works), 'errors': len(errors)}


class CrossrefLookup:
    """Record Crossref candidates for DOI-less titles; acceptance is decided later, offline."""

    def __init__(self, settings: BenchmarkSettings, client: HttpClient, contact: str | None = None) -> None:
        self.settings = settings.sources
        self.client = client
        self.contact = contact

    def lookup(self, titles: dict[str, int | None], destination: Path) -> dict[str, object]:
        path = destination / CROSSREF_SNAPSHOT
        snapshot = read_json(path) if path.exists() else {'lookups': {}}
        params_base = {'rows': self.settings.crossref_rows, 'select': 'DOI,title,author,issued,type,container-title'}
        if self.contact:
            params_base['mailto'] = self.contact
        fetched, failed = 0, 0
        for title, year in sorted(titles.items()):
            key = title_key(title)
            if not key or _cached(snapshot, key):
                continue
            try:
                response = self.client.get(
                    self.settings.crossref_api_url.rstrip('/') + '/works',
                    params=dict(params_base, **{'query.bibliographic': title}),
                )
            except SourceError as exc:
                failed += 1
                snapshot['lookups'][key] = {'title': title, 'year': year, 'error': str(exc), 'fetched_at': _now()}
                continue
            candidates = []
            for item in (response.get('message') or {}).get('items') or []:
                issued = ((item.get('issued') or {}).get('date-parts') or [[None]])[0][0]
                candidates.append(
                    {
                        'doi': (item.get('DOI') or '').lower(),
                        'title': (item.get('title') or [''])[0],
                        'year': issued,
                        'type': item.get('type'),
                        'container': (item.get('container-title') or [''])[0],
                        'family_names': [
                            author.get('family') for author in item.get('author') or [] if author.get('family')
                        ],
                    }
                )
            snapshot['lookups'][key] = {'title': title, 'year': year, 'candidates': candidates, 'fetched_at': _now()}
            fetched += 1
            write_json(path, snapshot)
        write_json(path, snapshot)
        return {'crossref_lookups': fetched, 'crossref_failures': failed}


class ArxivLookup:
    """Record arXiv candidates for titles without an arXiv ID; most CS conference papers have no DOI."""

    def __init__(
        self, settings: BenchmarkSettings, client: HttpClient, sleep: Callable[[float], None] = time.sleep
    ) -> None:
        self.settings = settings.sources
        self.client = client
        self.sleep = sleep

    def lookup(self, titles: dict[str, int | None], destination: Path) -> dict[str, object]:
        path = destination / ARXIV_SNAPSHOT
        snapshot = read_json(path) if path.exists() else {'lookups': {}}
        fetched, failed = 0, 0
        for title, year in sorted(titles.items()):
            key = title_key(title)
            if not key or _cached(snapshot, key):
                continue
            if fetched or failed:
                self.sleep(self.settings.arxiv_delay_seconds)
            phrase = ' '.join(word for word in key.split() if len(word) > 1)
            try:
                feed = self.client.get(
                    self.settings.arxiv_api_url,
                    params={'search_query': f'ti:"{phrase}"', 'max_results': self.settings.arxiv_rows},
                    xml=True,
                )
            except SourceError as exc:
                failed += 1
                snapshot['lookups'][key] = {'title': title, 'year': year, 'error': str(exc), 'fetched_at': _now()}
                continue
            candidates = []
            for entry in feed.findall(ATOM + 'entry'):
                identifier = (entry.findtext(ATOM + 'id') or '').rsplit('/abs/', 1)[-1]
                candidates.append(
                    {
                        'arxiv': identifier.split('v')[0] if identifier[:1].isdigit() else identifier,
                        'title': ' '.join((entry.findtext(ATOM + 'title') or '').split()),
                        'year': int((entry.findtext(ATOM + 'published') or '0')[:4] or 0) or None,
                        'authors': [author.findtext(ATOM + 'name') for author in entry.findall(ATOM + 'author')],
                    }
                )
            snapshot['lookups'][key] = {'title': title, 'year': year, 'candidates': candidates, 'fetched_at': _now()}
            fetched += 1
            write_json(path, snapshot)
        write_json(path, snapshot)
        return {'arxiv_lookups': fetched, 'arxiv_failures': failed}


class SnapshotCollector:
    def __init__(
        self,
        settings: BenchmarkSettings,
        registry: TruthSourceRegistry,
        orcid: OrcidFetcher,
        crossref: CrossrefLookup | None,
        arxiv: ArxivLookup | None = None,
    ) -> None:
        self.settings = settings
        self.registry = registry
        self.orcid = orcid
        self.crossref = crossref
        self.arxiv = arxiv

    def collect(self, person: BenchmarkPerson) -> dict[str, object]:
        destination = self.settings.paths.snapshots / person.person_id
        destination.mkdir(parents=True, exist_ok=True)
        summary: dict[str, object] = {'person_id': person.person_id}
        for spec in person.truth_sources:
            if spec.kind == 'orcid':
                summary.update(self.orcid.fetch(spec.locator, destination))
        start, end = person.window(self.settings)
        without_doi: dict[str, int | None] = {}
        without_arxiv: dict[str, int | None] = {}
        for spec in person.truth_sources:
            for record in self.registry.get(spec.kind).load(person, spec, destination):
                if record.year is not None and not start - 1 <= record.year <= end + 1:
                    continue
                if not record.doi:
                    without_doi[record.title] = record.year
                if not record.external_ids.get('arxiv'):
                    without_arxiv[record.title] = record.year
        if self.crossref is not None:
            summary.update(self.crossref.lookup(without_doi, destination))
        if self.arxiv is not None:
            summary.update(self.arxiv.lookup(without_arxiv, destination))
        return summary


def missing_snapshots(person: BenchmarkPerson, snapshot_dir: Path) -> list[str]:
    expected = [
        orcid_snapshot_name(spec.locator) if spec.kind == 'orcid' else spec.locator for spec in person.truth_sources
    ]
    return [name for name in expected if not (snapshot_dir / name).exists()]

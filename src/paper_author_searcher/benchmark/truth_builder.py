"""Turn source snapshots into a pending, publication-level ground truth for human review."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from paper_author_searcher.config import DEFAULT_MATCHING
from paper_author_searcher.evaluation_matching import GroundTruth, bibliographic_name_agrees
from paper_author_searcher.matching import canonical_doi, name_similarity, normalize
from paper_author_searcher.paper_authorship import byline_forms
from paper_author_searcher.status import AnnotationState

from .roster import BenchmarkPerson
from .settings import BenchmarkSettings
from .snapshots import ARXIV_SNAPSHOT, CROSSREF_SNAPSHOT, title_key
from .truth_sources import TruthRecord, TruthSourceRegistry

BUILDER_VERSION = 1
PREPRINT_TYPES = frozenset({'preprint', 'working_paper'})
MINIMUM_GROUPING_TITLE_TOKENS = 3


class _UnionFind:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, item: int) -> int:
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left: int, right: int) -> None:
        self.parent[self.find(left)] = self.find(right)


def _identifier_keys(record: TruthRecord) -> List[Tuple[str, str]]:
    keys = [('doi', canonical_doi(doi)) for doi in [record.doi, *record.related_dois] if doi]
    keys.extend((namespace, value) for namespace, value in record.external_ids.items())
    return keys


def _formal_first(records: Sequence[TruthRecord]) -> List[TruthRecord]:
    return sorted(
        records, key=lambda record: (record.work_type in PREPRINT_TYPES, -len(record.authors), record.year or 9999)
    )


class CrossrefAcceptance:
    """Accept a Crossref DOI only on an exact normalized title, close year and the target's family name."""

    def __init__(self, snapshot: dict, family_name: str, year_tolerance: int) -> None:
        self.lookups = snapshot.get('lookups') or {}
        self.family_name = normalize(family_name)
        self.year_tolerance = year_tolerance

    def accept(self, record: TruthRecord) -> Optional[dict]:
        key = title_key(record.title)
        for candidate in (self.lookups.get(key) or {}).get('candidates') or []:
            if normalize(candidate.get('title') or '') != key or not candidate.get('doi'):
                continue
            if record.year and candidate.get('year') and abs(record.year - candidate['year']) > self.year_tolerance:
                continue
            if self.family_name not in {normalize(name) for name in candidate.get('family_names') or []}:
                continue
            return candidate
        return None


class ArxivAcceptance:
    """Accept an arXiv ID only on an exact normalized title and the target's family name in the byline."""

    def __init__(self, snapshot: dict, family_name: str, year_tolerance: int) -> None:
        self.lookups = snapshot.get('lookups') or {}
        self.family_name = normalize(family_name)
        self.year_tolerance = year_tolerance

    def accept(self, record: TruthRecord) -> Optional[dict]:
        key = title_key(record.title)
        for candidate in (self.lookups.get(key) or {}).get('candidates') or []:
            if normalize(candidate.get('title') or '') != key or not candidate.get('arxiv'):
                continue
            if record.year and candidate.get('year') and abs(record.year - candidate['year']) > self.year_tolerance:
                continue
            families = {normalize(name).split()[-1] for name in candidate.get('authors') or [] if normalize(name)}
            if self.family_name in families:
                return candidate
        return None


class TruthBuilder:
    def __init__(self, settings: BenchmarkSettings, registry: TruthSourceRegistry) -> None:
        self.settings = settings
        self.registry = registry

    def build(self, person: BenchmarkPerson) -> dict:
        snapshot_dir = self.settings.paths.snapshots / person.person_id
        records, source_summaries = [], []
        for spec in person.truth_sources:
            loaded = self.registry.get(spec.kind).load(person, spec, snapshot_dir)
            records.extend(loaded)
            source_summaries.append(
                {'kind': spec.kind, 'locator': spec.locator, 'url': spec.url, 'records': len(loaded)}
            )
        identifier_provenance = self._enrich(person, records, snapshot_dir)
        groups = self._group(records)
        start, end = person.window(self.settings)
        papers, excluded = [], []
        for group in groups:
            reason = self._exclusion(group, start, end)
            entry = self._entry(person, group, identifier_provenance)
            if reason is None and not entry.get('dois') and not entry.get('external_ids') and not entry.get('authors'):
                reason = 'no_identifier_or_authors'
            if reason:
                excluded.append(
                    {
                        'reason': reason,
                        'title': entry['title'],
                        'year': entry['year'],
                        'work_types': entry['work_types'],
                        'evidence': entry['annotation_evidence'],
                    }
                )
            else:
                papers.append(entry)
        papers.sort(key=lambda paper: (paper['year'] or 0, normalize(paper['title'])))
        truth = {
            'schema_version': 1,
            'complete_positive_list': False,
            'metadata': {
                'annotation_state': AnnotationState.PENDING_HUMAN_REVIEW,
                'reviewer': '',
                'reviewed_at': '',
                'person_id': person.person_id,
                'dataset_id': self.settings.dataset_id,
                'search_input': {'name': person.search_input.name, 'university': person.search_input.university},
                'scope': {
                    'year_start': start,
                    'year_end': end,
                    'included_types': list(self.settings.type_policy.included),
                    'excluded_types': list(self.settings.type_policy.excluded),
                    'unknown_types_included': self.settings.type_policy.unknown_included,
                    'version_policy': 'One entry per publication; preprints and published versions with the '
                    'same identifier or normalized title (within the configured year '
                    'tolerance) are grouped so finding any version counts.',
                },
                'provenance': 'Researcher-maintained bibliographies only; no search pipeline output is read.',
                'builder_version': BUILDER_VERSION,
                'built_at': datetime.now(timezone.utc).isoformat(),
                'sources': source_summaries,
                'excluded_counts': _count(item['reason'] for item in excluded),
            },
            'papers': papers,
            'excluded': excluded,
        }
        if person.orcid:
            truth['professor'] = {'orcid': person.orcid, 'author_ids': {}, 'author_ids_complete': False}
        GroundTruth.from_dict(truth)
        return truth

    def _enrich(self, person: BenchmarkPerson, records: List[TruthRecord], snapshot_dir: Path) -> Dict[int, dict]:
        family = person.search_input.name.split()[-1]
        provenance: Dict[int, dict] = {}
        arxiv_path = snapshot_dir / ARXIV_SNAPSHOT
        if arxiv_path.exists():
            with arxiv_path.open(encoding='utf-8') as handle:
                arxiv = ArxivAcceptance(json.load(handle), family, self.settings.version_grouping_year_tolerance)
            for record in records:
                if record.external_ids.get('arxiv'):
                    continue
                candidate = arxiv.accept(record)
                if candidate:
                    record.external_ids['arxiv'] = candidate['arxiv']
                    provenance[id(record)] = {
                        'arxiv': candidate['arxiv'],
                        'method': 'arxiv_title_lookup',
                        'arxiv_title': candidate['title'],
                        'arxiv_year': candidate.get('year'),
                    }
        path = snapshot_dir / CROSSREF_SNAPSHOT
        if not path.exists():
            return provenance
        with path.open(encoding='utf-8') as handle:
            acceptance = CrossrefAcceptance(json.load(handle), family, self.settings.sources.crossref_year_tolerance)
        for record in records:
            if record.doi:
                continue
            candidate = acceptance.accept(record)
            if candidate:
                record.doi = candidate['doi']
                provenance.setdefault(id(record), {}).update(
                    {
                        'doi': candidate['doi'],
                        'doi_method': 'crossref_title_lookup',
                        'crossref_title': candidate['title'],
                        'crossref_year': candidate.get('year'),
                    }
                )
        return provenance

    def _group(self, records: List[TruthRecord]) -> List[List[TruthRecord]]:
        union = _UnionFind(len(records))
        owners: Dict[Tuple[str, str], int] = {}
        titles: Dict[str, List[int]] = {}
        tolerance = self.settings.version_grouping_year_tolerance
        for index, record in enumerate(records):
            for key in _identifier_keys(record):
                if key in owners:
                    union.union(index, owners[key])
                else:
                    owners[key] = index
            key = normalize(record.title)
            if len(key.split()) < MINIMUM_GROUPING_TITLE_TOKENS:
                continue
            for other in titles.get(key, []):
                other_year = records[other].year
                if record.year is None or other_year is None or abs(record.year - other_year) <= tolerance:
                    union.union(index, other)
            titles.setdefault(key, []).append(index)
        grouped: Dict[int, List[TruthRecord]] = {}
        for index, record in enumerate(records):
            grouped.setdefault(union.find(index), []).append(record)
        return list(grouped.values())

    def _exclusion(self, group: List[TruthRecord], start: int, end: int) -> Optional[str]:
        year = _publication_year(group)
        if year is None:
            return 'missing_year'
        if not start <= year <= end:
            return 'outside_year_window'
        if not any(self.settings.type_policy.includes(record.work_type) for record in group):
            return 'excluded_publication_type'
        return None

    def _entry(self, person: BenchmarkPerson, group: List[TruthRecord], identifier_provenance: Dict[int, dict]) -> dict:
        ordered = _formal_first(group)
        main = ordered[0]
        authors = max((record.authors for record in group), key=len)
        dois = sorted(
            {canonical_doi(doi)[len('doi:') :] for record in group for doi in [record.doi, *record.related_dois] if doi}
        )
        external: Dict[str, str] = {}
        for record in ordered:
            for namespace, value in record.external_ids.items():
                external.setdefault(namespace, value)
        keys = sorted('{}:{}'.format(*key) for record in group for key in _identifier_keys(record))
        seed = '|'.join(keys) or normalize(main.title) + '|' + str(_publication_year(group))
        entry = {
            'evaluation_id': '{}:{}'.format(person.person_id, hashlib.sha256(seed.encode('utf-8')).hexdigest()[:12]),
            'ownership': 'uncertain',
            'proposed_ownership': 'belongs',
            'annotation_status': AnnotationState.PENDING_HUMAN_REVIEW,
            'title': main.title,
            'year': _publication_year(group),
            'authors': list(authors),
            # Kept out of ``venue``: the evaluator refuses a match when two non-empty venue strings differ,
            # and a CV's "NeurIPS 2025" never equals an index's "Advances in Neural Information ...".
            'listed_venue': main.venue,
            'work_types': sorted({record.work_type or 'unknown' for record in group}),
            'truth_sources': sorted({record.source_kind for record in group}),
            'annotation_evidence': sorted({record.evidence for record in group}),
            'review_flags': [],
        }
        if dois:
            entry['dois'] = dois
        if external:
            entry['external_ids'] = external
        aliases = []
        seen = {(normalize(main.title), entry['year'])}
        for record in ordered[1:]:
            marker = (normalize(record.title), record.year)
            if marker not in seen and record.year is not None and record.authors:
                seen.add(marker)
                aliases.append({'title': record.title, 'year': record.year, 'authors': record.authors})
        tolerance = self.settings.metadata_year_tolerance
        if entry['year'] is not None and authors:
            for offset in range(-tolerance, tolerance + 1):
                marker = (normalize(main.title), entry['year'] + offset)
                if offset and marker not in seen:
                    seen.add(marker)
                    aliases.append({'title': main.title, 'year': entry['year'] + offset, 'authors': list(authors)})
        if aliases:
            entry['metadata_aliases'] = aliases
        provenance = [identifier_provenance[id(record)] for record in group if id(record) in identifier_provenance]
        if provenance:
            entry['identifier_provenance'] = provenance
            entry['review_flags'].append('identifier_from_title_lookup')
        if authors and not _target_in_byline(person.search_input.name, authors):
            entry['proposed_ownership'] = 'uncertain'
            entry['review_flags'].append('target_name_not_found_in_byline')
        if not authors:
            entry['review_flags'].append('byline_unavailable_in_sources')
        if len(group) > 1:
            entry['review_flags'].append(f'grouped_{len(group)}_source_records')
        return entry


def _publication_year(group: Iterable[TruthRecord]) -> Optional[int]:
    group = list(group)
    formal = [record.year for record in group if record.year and record.work_type not in PREPRINT_TYPES]
    years = formal or [record.year for record in group if record.year]
    return min(years) if years else None


def _target_in_byline(name: str, authors: Sequence[str]) -> bool:
    threshold = DEFAULT_MATCHING.compatible_name
    return any(
        name_similarity(name, form) >= threshold or bibliographic_name_agrees(name, form)
        for author in authors
        for form in byline_forms(author)
    )


def _count(values: Iterable[str]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts

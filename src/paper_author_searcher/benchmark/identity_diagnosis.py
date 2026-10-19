"""Explain identity outcomes against truth: which candidate profiles actually hold the person's papers.

This is evaluation-only tooling. It reads truth to label failure modes (split profiles, wrong person, target not
retrieved) and must never feed back into the search system's decisions.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

from paper_author_searcher.config import DEFAULT_MATCHING
from paper_author_searcher.http import SourceError
from paper_author_searcher.matching import canonical_doi, normalize
from paper_author_searcher.status import UniversityStatus

from .roster import BenchmarkPerson

# A failure in these sources can hide the target; DBLP's anti-bot page is a known, constant gap.
SEARCH_SOURCES = ('openalex', 'semantic_scholar')


class Diagnosis(str, Enum):
    RESOLVED_COMPLETE = 'resolved_complete'  # chosen IDs hold all truth-bearing profiles
    RESOLVED_FRAGMENT = 'resolved_fragment'  # chosen IDs right, but other split profiles hold truth too
    RESOLVED_WRONG_PERSON = 'resolved_wrong_person'  # chosen IDs hold no truth; another candidate does
    STUCK_SPLIT_PROFILES = 'stuck_split_profiles'  # several contenders are fragments of the same person
    STUCK_TRUE_COMPETITOR = 'stuck_true_competitor'  # one contender is right, a real namesake is close
    TARGET_NOT_RETRIEVED = 'target_not_retrieved'  # no candidate holds any truth paper
    INCONCLUSIVE = 'inconclusive_source_errors'  # nothing found, but searches or listings failed
    NO_TRUTH = 'no_truth'


@dataclass
class CandidateEvidence:
    author_ids: Dict[str, List[str]]
    match_score: float
    status: str
    university_status: str
    truth_hits: Set[str] = field(default_factory=set)
    window_works: int = 0
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            'author_ids': self.author_ids,
            'match_score': self.match_score,
            'status': self.status,
            'university_status': self.university_status,
            'truth_hits': len(self.truth_hits),
            'window_works': self.window_works,
            'errors': self.errors,
        }


class TruthIndex:
    """DOI / arXiv / normalized-title lookup over truth publications, for diagnosis only."""

    def __init__(self, truth: dict) -> None:
        self.keys: Dict[Tuple[str, str], str] = {}
        for paper in truth.get('papers') or []:
            proposed = paper.get('ownership') == 'uncertain' and paper.get('proposed_ownership') == 'belongs'
            if paper.get('ownership') != 'belongs' and not proposed:
                continue
            identifier = paper['evaluation_id']
            for doi in paper.get('dois') or []:
                self.keys[('doi', canonical_doi(doi))] = identifier
            arxiv = (paper.get('external_ids') or {}).get('arxiv')
            if arxiv:
                self.keys[('arxiv', arxiv)] = identifier
            for description in [paper] + (paper.get('metadata_aliases') or []):
                if description.get('title'):
                    self.keys[('title', normalize(description['title']))] = identifier
        self.size = len(set(self.keys.values()))

    def match(self, work: dict) -> Optional[str]:
        for key in work_keys(work):
            if key in self.keys:
                return self.keys[key]
        return None


def work_keys(work: dict) -> Iterable[Tuple[str, str]]:
    if work.get('doi'):
        yield 'doi', canonical_doi(work['doi'])
    if work.get('arxiv'):
        yield 'arxiv', work['arxiv']
    if work.get('title'):
        yield 'title', normalize(work['title'])


class WorkLister:
    """Lists an author profile's works in a year window (OpenAlex and Semantic Scholar)."""

    def __init__(self, client, openalex_url: str, semantic_scholar_url: str, api_keys: Optional[dict] = None) -> None:
        self.client = client
        self.openalex_url = openalex_url.rstrip('/')
        self.semantic_scholar_url = semantic_scholar_url.rstrip('/')
        self.api_keys = api_keys or {}

    def works(self, source: str, author_id: str, start: int, end: int) -> List[dict]:
        if source == 'openalex':
            return self._openalex(author_id, start, end)
        if source == 'semantic_scholar':
            return self._semantic_scholar(author_id, start, end)
        return []

    def _openalex(self, author_id: str, start: int, end: int) -> List[dict]:
        works: List[dict] = []
        cursor: Optional[str] = '*'
        while cursor:
            data = self.client.get(
                self.openalex_url + '/works',
                {
                    'filter': f'authorships.author.id:{author_id},publication_year:{start}-{end}',
                    'select': 'id,doi,title,publication_year,ids',
                    'per-page': 200,
                    'cursor': cursor,
                    'api_key': self.api_keys.get('openalex'),
                },
            )
            for item in data.get('results') or []:
                works.append({'doi': item.get('doi'), 'title': item.get('title'), 'year': item.get('publication_year')})
            cursor = (data.get('meta') or {}).get('next_cursor') if data.get('results') else None
        return works

    def _semantic_scholar(self, author_id: str, start: int, end: int) -> List[dict]:
        headers = {'x-api-key': self.api_keys['semantic_scholar']} if self.api_keys.get('semantic_scholar') else None
        data = self.client.get(
            f'{self.semantic_scholar_url}/author/{author_id}/papers',
            {'fields': 'title,year,externalIds', 'limit': 1000},
            headers,
        )
        works = []
        for item in data.get('data') or []:
            if item.get('year') is None or not start <= item['year'] <= end:
                continue
            external = item.get('externalIds') or {}
            works.append(
                {
                    'doi': external.get('DOI'),
                    'arxiv': external.get('ArXiv'),
                    'title': item.get('title'),
                    'year': item.get('year'),
                }
            )
        return works


class IdentityDiagnoser:
    def __init__(
        self,
        rank: Callable,
        lister: WorkLister,
        window_slack: int = 1,
        maximum_candidates: int = 8,
        compatible_name: float = DEFAULT_MATCHING.compatible_name,
    ) -> None:
        self.rank = rank
        self.lister = lister
        self.window_slack = window_slack
        self.maximum_candidates = maximum_candidates
        self.compatible_name = compatible_name

    def diagnose(
        self,
        person: BenchmarkPerson,
        truth: Optional[dict],
        window: Tuple[int, int],
        selected_ids: Optional[Dict[str, List[str]]],
        run_status: str,
    ) -> dict:
        profiles, _groups, errors = self.rank(person.search_input.to_query())
        index = TruthIndex(truth or {})
        start, end = window
        contenders = [profile for profile in profiles if profile.university_status != UniversityStatus.MISMATCH]
        contenders = contenders[: self.maximum_candidates]
        evidence = []
        for profile in contenders:
            item = CandidateEvidence(
                profile.author_id_aliases or {k: [v] for k, v in profile.author_ids.items()},
                profile.match_score,
                profile.status,
                profile.university_status,
            )
            for source, author_ids in item.author_ids.items():
                for author_id in author_ids:
                    try:
                        works = self.lister.works(source, author_id, start - self.window_slack, end + self.window_slack)
                    except SourceError as exc:
                        item.errors.append(f'{source}:{author_id} {exc}')
                        continue
                    item.window_works += len(works)
                    item.truth_hits.update(filter(None, (index.match(work) for work in works)))
            evidence.append(item)
        failed = any(source in errors for source in SEARCH_SOURCES) or any(item.errors for item in evidence)
        label = self._label(index, evidence, selected_ids, run_status, failed)
        bearing = [item for item in evidence if item.truth_hits]
        union = set().union(*(item.truth_hits for item in evidence)) if evidence else set()
        top_margin = round(profiles[0].match_score - profiles[1].match_score, 3) if len(profiles) > 1 else None
        return {
            'person_id': person.person_id,
            'run_status': run_status,
            'diagnosis': label.value,
            'source_failures': failed,
            'truth_publications': index.size,
            'truth_found_in_any_candidate': len(union),
            'truth_bearing_candidates': len(bearing),
            'candidates_considered': len(evidence),
            'top_score_margin': top_margin,
            'source_errors': errors,
            'candidates': [item.to_dict() for item in evidence],
        }

    @staticmethod
    def _label(
        index: TruthIndex,
        evidence: List[CandidateEvidence],
        selected_ids: Optional[Dict[str, List[str]]],
        run_status: str,
        failed: bool = False,
    ) -> Diagnosis:
        if not index.size:
            return Diagnosis.NO_TRUTH
        bearing = [item for item in evidence if item.truth_hits]
        if not bearing:
            return Diagnosis.INCONCLUSIVE if failed else Diagnosis.TARGET_NOT_RETRIEVED
        if selected_ids:
            chosen = {(source, value) for source, values in selected_ids.items() for value in values}
            holds = [
                item
                for item in bearing
                if chosen & {(source, value) for source, values in item.author_ids.items() for value in values}
            ]
            if not holds:
                return Diagnosis.RESOLVED_WRONG_PERSON
            return Diagnosis.RESOLVED_COMPLETE if len(holds) == len(bearing) else Diagnosis.RESOLVED_FRAGMENT
        if len(bearing) >= 2:
            return Diagnosis.STUCK_SPLIT_PROFILES
        return Diagnosis.STUCK_TRUE_COMPETITOR


def is_inconclusive(diagnosis: dict) -> bool:
    """Also recognises reports written before the inconclusive label existed."""
    if diagnosis.get('diagnosis') == Diagnosis.INCONCLUSIVE.value:
        return True
    failed = any(source in (diagnosis.get('source_errors') or {}) for source in SEARCH_SOURCES) or any(
        candidate.get('errors') for candidate in diagnosis.get('candidates') or []
    )
    return failed and not diagnosis.get('truth_found_in_any_candidate')


def markdown(diagnoses: List[dict]) -> str:
    lines = [
        '| Person | Run | Diagnosis | Truth | Found in any candidate | Truth-bearing candidates | Margin |',
        '|---|---|---|---:|---:|---:|---:|',
    ]
    for item in diagnoses:
        lines.append(
            '| {person_id} | {run_status} | {diagnosis} | {truth_publications} | '
            '{truth_found_in_any_candidate} | {truth_bearing_candidates} | {top_score_margin} |'.format(**item)
        )
    counts: Dict[str, int] = {}
    for item in diagnoses:
        counts[item['diagnosis']] = counts.get(item['diagnosis'], 0) + 1
    lines += ['', 'Counts: ' + ', '.join(f'{key}={value}' for key, value in sorted(counts.items()))]
    return '\n'.join(lines) + '\n'

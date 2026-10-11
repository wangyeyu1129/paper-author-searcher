"""Ground-truth matching independent of retrieval/ownership heuristics."""
from dataclasses import dataclass, field
from typing import List, Optional

from .matching import canonical_doi, normalize
from .paper_authorship import byline_forms

LABELS = frozenset({'belongs', 'not_belongs', 'uncertain'})
MATCHING_RULES_VERSION = 2


@dataclass
class GroundTruth:
    papers: List[dict]
    complete_positive_list: bool = False
    professor: Optional[dict] = None
    metadata: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ValueError('Ground truth must be an object')
        if data.get('schema_version') != 1:
            raise ValueError('Ground truth requires schema_version=1')
        papers = data.get('papers')
        if not isinstance(papers, list):
            raise ValueError('Ground truth papers must be a list')
        ids = set()
        identifiers = {}
        for paper in papers:
            if not isinstance(paper, dict):
                raise ValueError('Ground truth entries must be objects')
            evaluation_id = paper.get('evaluation_id')
            if not isinstance(evaluation_id, str) or not evaluation_id or evaluation_id in ids:
                raise ValueError('Each ground-truth paper needs a unique evaluation_id')
            ids.add(evaluation_id)
            if not isinstance(paper.get('ownership'), str) or paper['ownership'] not in LABELS:
                raise ValueError('Ownership must be belongs, not_belongs or uncertain')
            _validate_metadata(paper)
            evidence = paper.get('annotation_evidence', [])
            if not isinstance(evidence, list) or not all(isinstance(item, str) for item in evidence):
                raise ValueError('annotation_evidence must be a list of strings')
            if not isinstance(paper.get('metadata_aliases', []), list):
                raise ValueError('metadata_aliases must be a list')
            for alias in paper.get('metadata_aliases', []):
                if not isinstance(alias, dict):
                    raise ValueError('metadata_aliases entries must be objects')
                _validate_metadata(alias)
            keys = stable_identifiers(paper)
            if not keys and metadata_key(paper) is None:
                raise ValueError('Ground truth needs stable identifiers or title, year and authors')
            for key in keys:
                if key in identifiers:
                    raise ValueError('Stable identifier assigned to multiple evaluation IDs: {}'.format(key))
                identifiers[key] = evaluation_id
        complete = data.get('complete_positive_list', False)
        if not isinstance(complete, bool):
            raise ValueError('complete_positive_list must be boolean')
        professor = data.get('professor')
        if professor is not None:
            if not isinstance(professor, dict):
                raise ValueError('professor must be an object')
            author_id_map = professor.get('author_ids', {})
            if not isinstance(author_id_map, dict):
                raise ValueError('professor author_ids must be an object')
            for source, author_ids in author_id_map.items():
                if not isinstance(author_ids, list) or not all(isinstance(value, str) for value in author_ids):
                    raise ValueError('professor author_ids must map sources to string lists')
            if not isinstance(professor.get('author_ids_complete', False), bool):
                raise ValueError('author_ids_complete must be boolean')
            if 'orcid' in professor and not isinstance(professor['orcid'], str):
                raise ValueError('professor orcid must be a string')
        metadata = data.get('metadata', {})
        if not isinstance(metadata, dict):
            raise ValueError('metadata must be an object')
        return cls(papers, complete, professor, metadata)


def _validate_metadata(paper):
    for key in ('title', 'venue'):
        if paper.get(key) is not None and not isinstance(paper[key], str):
            raise ValueError(key + ' must be a string')
    if paper.get('year') is not None:
        if not isinstance(paper['year'], int) or isinstance(paper['year'], bool):
            raise ValueError('year must be an integer')
    if 'authors' in paper:
        if not isinstance(paper['authors'], list) or not all(isinstance(name, str) for name in paper['authors']):
            raise ValueError('authors must be a list of strings')


def stable_identifiers(paper):
    keys = set()
    doi_list = paper.get('dois') or []
    if not isinstance(doi_list, list):
        raise ValueError('dois must be a list')
    values = list(doi_list)
    if paper.get('doi'):
        values.append(paper['doi'])
    for doi in values:
        if not isinstance(doi, str):
            raise ValueError('DOIs must be strings')
        if doi.strip():
            keys.add(('doi', canonical_doi(doi)))
    external_ids = paper.get('external_ids') or {}
    if not isinstance(external_ids, dict):
        raise ValueError('external_ids must be an object')
    for namespace, value in external_ids.items():
        if value is None or not str(value).strip():
            continue
        namespace = namespace.casefold()
        if namespace == 'doi':
            keys.add(('doi', canonical_doi(str(value))))
        else:
            keys.add((namespace, str(value).strip()))
    records = paper.get('source_records') or []
    if not isinstance(records, list):
        raise ValueError('source_records must be a list')
    if paper.get('source') and paper.get('source_paper_id'):
        records = records + [{'source': paper['source'], 'paper_id': paper['source_paper_id']}]
    for record in records:
        if not isinstance(record, dict):
            raise ValueError('source_records entries must be objects')
        if not record.get('source') or not record.get('paper_id'):
            raise ValueError('source_records require source and paper_id')
        source = record['source'].casefold()
        value = str(record['paper_id'])
        keys.add(('source:' + source, value))
        keys.add((source, value))
    return keys


def metadata_key(paper):
    _validate_metadata(paper)
    title = paper.get('title')
    year = paper.get('year')
    authors = paper.get('authors') or []
    if not title or year is None or not authors:
        return None
    normalized_authors = tuple(sorted(set(normalize(byline_forms(name)[-1]) for name in authors)))
    if not all(normalized_authors):
        return None
    normalized_title = normalize(title)
    if not normalized_title:
        return None
    return normalized_title, year, normalized_authors


@dataclass
class TruthMatch:
    evaluation_id: Optional[str] = None
    candidates: List[str] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)
    decision: str = 'unmatched'


class GroundTruthMatcher:
    def __init__(self, truth):
        self.truth = truth
        self.by_id = {paper['evaluation_id']: paper for paper in truth.papers}
        self.identifiers = {}
        self.metadata = {}
        self.title_year = {}
        for paper in truth.papers:
            evaluation_id = paper['evaluation_id']
            for key in stable_identifiers(paper):
                self.identifiers[key] = evaluation_id
            for description in [paper] + (paper.get('metadata_aliases') or []):
                key = metadata_key(description)
                if key is not None:
                    venue = description.get('venue') or paper.get('venue') or ''
                    venues = self.metadata.setdefault(key, {}).setdefault(evaluation_id, set())
                    venues.add(normalize(venue))
                    self.title_year.setdefault(key[:2], []).append((evaluation_id, description, normalize(venue)))

    def match(self, paper):
        keys = stable_identifiers(paper)
        found = set()
        evidence = []
        for key in sorted(keys):
            if key in self.identifiers:
                found.add(self.identifiers[key])
                evidence.append('Ground-truth identifier match: {}={}'.format(*key))
        if found:
            return self._resolve(paper, found, evidence, 'stable_identifier')
        matches = self.metadata.get(metadata_key(paper), {})
        compatible = set()
        venue = normalize(paper.get('venue') or '')
        for evaluation_id, expected_venues in matches.items():
            if venue and '' not in expected_venues and venue not in expected_venues:
                continue
            compatible.add(evaluation_id)
        if compatible:
            return self._resolve(
                paper, compatible, ['Exact normalized title, year and full author set match'], 'metadata',
            )
        title_year = (normalize(paper.get('title') or ''), paper.get('year'))
        signatures = set()
        for evaluation_id, description, expected_venue in self.title_year.get(title_year, []):
            if venue and expected_venue and venue != expected_venue:
                continue
            if _bibliographic_authors_agree(paper.get('authors') or [], description.get('authors') or []):
                signatures.add(evaluation_id)
        if signatures:
            return self._resolve(paper, signatures,
                                 ['Exact title/year and complete author signatures agree; bibliographic initials/middle-name omissions normalized'],
                                 'metadata_signatures')
        return TruthMatch(evidence=['No adjudicated ground-truth match'])

    def _resolve(self, paper, ids, evidence, method):
        if len(ids) != 1:
            return TruthMatch(candidates=sorted(ids), evidence=evidence + ['Multiple truth identities match'],
                              decision='ambiguous')
        evaluation_id = next(iter(ids))
        observed_dois = {key for key in stable_identifiers(paper) if key[0] == 'doi'}
        expected_dois = {key for key in stable_identifiers(self.by_id[evaluation_id]) if key[0] == 'doi'}
        if observed_dois and expected_dois and not observed_dois.intersection(expected_dois):
            return TruthMatch(candidates=[evaluation_id], evidence=evidence + ['Conflicting DOI/version evidence'],
                              decision='identifier_conflict')
        return TruthMatch(evaluation_id, [evaluation_id], evidence, method)


def _bibliographic_name_agrees(left, right):
    first = normalize(byline_forms(left)[-1]).split()
    second = normalize(byline_forms(right)[-1]).split()
    if first == second:
        return True
    if len(first) < 2 or len(second) < 2 or first[-1] != second[-1]:
        return False
    if not _name_token_agrees(first[0], second[0]):
        return False
    middle_left, middle_right = first[1:-1], second[1:-1]
    if not middle_left or not middle_right:
        return True
    if len(middle_left) != len(middle_right):
        return False
    return all(_name_token_agrees(a, b) for a, b in zip(middle_left, middle_right))


def _name_token_agrees(left, right):
    if left == right:
        return True
    return min(len(left), len(right)) == 1 and left[0] == right[0]


def _bibliographic_authors_agree(left, right):
    if not left or len(left) != len(right):
        return False
    choices = []
    for name in left:
        choices.append([index for index, other in enumerate(right) if _bibliographic_name_agrees(name, other)])
    # A complete matching avoids greedily assigning an abbreviated name to the wrong author.
    assigned = {}
    def assign(position, visited):
        for index in choices[position]:
            if index in visited:
                continue
            visited.add(index)
            if index not in assigned or assign(assigned[index], visited):
                assigned[index] = position
                return True
        return False
    return all(assign(position, set()) for position in range(len(left)))

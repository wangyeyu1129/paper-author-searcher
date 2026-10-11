"""Exact work grouping plus explicit links between probable related versions."""
from dataclasses import asdict
from itertools import combinations

from .config import DEFAULT_DEDUPLICATION
from .matching import canonical_doi, normalize, name_similarity
from .models import Paper
from .paper_types import classify_record, publication_form


def author_signatures_match(left, right, config=DEFAULT_DEDUPLICATION):
    if name_similarity(left, right) >= config.author_similarity:
        return True
    if not config.allow_author_initials:
        return False
    left_words = normalize(left).split()
    right_words = normalize(right).split()
    if len(left_words) < 2 or len(right_words) < 2:
        return False
    if left_words[-1] != right_words[-1] or left_words[0][0] != right_words[0][0]:
        return False
    if len(left_words[0]) != 1 and len(right_words[0]) != 1:
        return False
    middle_left, middle_right = left_words[1:-1], right_words[1:-1]
    if len(middle_left) != len(middle_right):
        return False
    for first, second in zip(middle_left, middle_right):
        if first == second:
            continue
        if first[0] != second[0] or min(len(first), len(second)) != 1:
            return False
    return True


def shared_authors(left, right, config=DEFAULT_DEDUPLICATION):
    used = set()
    count = 0
    for author in left.authors:
        for index, other in enumerate(right.authors):
            if index in used:
                continue
            if author_signatures_match(author, other, config):
                used.add(index)
                count += 1
                break
    return count


def _metadata_agrees(left, right, config):
    if not left.title or normalize(left.title) != normalize(right.title):
        return False
    if left.year is None or left.year != right.year:
        return False
    count = shared_authors(left, right, config)
    total = max(len(left.authors), len(right.authors))
    if total == 0 or count < config.minimum_shared_authors:
        return False
    return count / total >= config.author_overlap


def _stable_ids(record):
    ids = set()
    if record.source_paper_id:
        ids.add(('source:' + record.source, record.source_paper_id))
    for namespace, value in record.external_ids.items():
        if value and namespace.lower() != 'doi':
            ids.add((namespace.lower(), str(value).strip()))
    if record.source == 'dblp':
        ids.add(('dblp', record.source_paper_id))
    if record.doi:
        ids.add(('doi', canonical_doi(record.doi)))
    return ids


def duplicate_evidence(left, right, config=DEFAULT_DEDUPLICATION):
    shared = _stable_ids(left) & _stable_ids(right)
    conflicting_dois = left.doi and right.doi and canonical_doi(left.doi) != canonical_doi(right.doi)
    if conflicting_dois:
        return []
    if shared:
        return ['Shared stable identifier: {}={}'.format(*identifier) for identifier in sorted(shared)]
    # Distinct DOIs remain separate versions unless another stable identifier agrees.
    if left.doi and right.doi and canonical_doi(left.doi) != canonical_doi(right.doi):
        return []
    if not _metadata_agrees(left, right, config):
        return []
    left_form, right_form = publication_form(left), publication_form(right)
    if left_form != 'unknown' and right_form != 'unknown' and left_form != right_form:
        return []
    if left.venue and right.venue:
        if normalize(left.venue) != normalize(right.venue):
            return []
    if not left.venue and not right.venue:
        return []
    return ['Matching normalized title, year, compatible authors and publication metadata']


def deduplicate_papers(records, config=DEFAULT_DEDUPLICATION):
    groups = []
    index = {}
    for record in records:
        classify_record(record)
        keys = list(_stable_ids(record))
        if record.title:
            keys.append(('title-year', normalize(record.title), record.year))
        candidates = set()
        for key in keys:
            candidates.update(index.get(key, set()))
        selected = None
        for group_index in sorted(candidates):
            if all(duplicate_evidence(record, other, config) for other in groups[group_index]):
                selected = group_index
                break
        if selected is None:
            selected = len(groups)
            groups.append([])
        groups[selected].append(record)
        for key in keys:
            index.setdefault(key, set()).add(selected)
    papers = []
    for group in groups:
        values = asdict(group[0])
        evidence = set()
        for left, right in combinations(group, 2):
            evidence.update(duplicate_evidence(left, right, config))
        for record in group[1:]:
            for key in ('doi', 'abstract', 'venue', 'paper_url', 'year'):
                if not values[key]:
                    values[key] = getattr(record, key)
        if values['doi']:
            values['doi'] = canonical_doi(values['doi'])[len('doi:'):]
        values.update(_combine_authorship_metadata(group))
        categories = {record.paper_type for record in group} - {'unknown'}
        values['type_evidence'] = sorted({
            '{}:{}: {}'.format(record.source, record.source_paper_id, message)
            for record in group for message in record.type_evidence
        })
        if categories == {'research_paper', 'review'}:
            values['paper_type'] = 'review'
        elif len(categories) == 1:
            values['paper_type'] = next(iter(categories))
        elif categories:
            values['paper_type'] = 'unknown'
            values['type_evidence'] = ['Conflicting source classifications: ' + ', '.join(sorted(categories))]
        papers.append(Paper(**values, records=list(group), deduplication_evidence=sorted(evidence)))
    _link_related_versions(papers, config)
    return papers


def _combine_authorship_metadata(group):
    details = []
    source_ids = []
    external_values = {}
    errors = set()
    for record in group:
        source_ids.append({
            'source': record.source, 'paper_id': record.source_paper_id,
            'author_id': record.source_author_id, 'external_ids': dict(record.external_ids),
            'target_author_present': record.target_author_present,
            'authors_complete': record.authors_complete,
        })
        for author in record.author_details:
            values = dict(author)
            values['source'] = record.source
            values['source_paper_id'] = record.source_paper_id
            details.append(values)
        for namespace, value in record.external_ids.items():
            external_values.setdefault(namespace, set()).add(value)
        errors.update(record.metadata_errors)
    external_ids = {}
    for namespace, values in external_values.items():
        if len(values) == 1:
            external_ids[namespace] = next(iter(values))
    observations = {record.target_author_present for record in group}
    present = None
    if True in observations and False not in observations:
        present = True
    elif observations == {False}:
        present = False
    completeness = None
    if all(record.authors_complete is True for record in group):
        completeness = True
    elif any(record.authors_complete is False for record in group):
        completeness = False
    return {
        'author_details': details, 'external_ids': external_ids,
        'target_author_present': present, 'source_identifiers': source_ids,
        'authors_complete': completeness,
        'metadata_errors': sorted(errors),
    }


def _link_related_versions(papers, config):
    titles = {}
    for paper in papers:
        titles.setdefault(normalize(paper.title), []).append(paper)
    for candidates in titles.values():
        for left, right in combinations(candidates, 2):
            count = shared_authors(left, right, config)
            total = max(len(left.authors), len(right.authors))
            if not total or count < config.minimum_shared_authors:
                continue
            if count / total < config.author_overlap:
                continue
            # Related research is not proof that these are the same publication/version.
            reason = 'Possible related version: same normalized title and compatible authors; identity of the research is unconfirmed'
            for paper, related in ((left, right), (right, left)):
                identifiers = []
                for record in related.records:
                    identifiers.append({
                        'source': record.source, 'paper_id': record.source_paper_id, 'doi': record.doi,
                    })
                paper.related_versions.append({'identifiers': identifiers, 'evidence': reason})

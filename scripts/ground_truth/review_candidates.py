"""Validate explicit human CSV annotations; never infer labels or compute metrics."""
import argparse
import csv
import json
from collections import Counter
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from scripts.ground_truth.build_candidates import REVIEW_COLUMNS, read_json, write_json


CHECKS = ('identity_checked', 'metadata_checked', 'type_checked', 'version_checked')
TRUE_VALUES = {'true', 'yes', '1'}


def _date(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (TypeError, ValueError):
        raise ValueError('reviewed_at must be an ISO-8601 timestamp')
    if parsed.tzinfo is None:
        raise ValueError('reviewed_at must include a timezone')


def _evidence(value):
    urls = json.loads(value or '[]')
    if not isinstance(urls, list) or not all(isinstance(url, str) for url in urls):
        raise ValueError('evidence_urls_json must contain a JSON string array')
    for url in urls:
        parsed = urlsplit(url)
        if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('Human evidence must use public-reference HTTP(S) URLs without credentials')
    return list(dict.fromkeys(urls))


def _review_metadata(paper, row):
    metadata = {name: deepcopy(paper.get(name)) for name in ('title', 'authors', 'year', 'doi', 'publication_type', 'venue')}
    for name in ('title', 'doi', 'publication_type', 'venue'):
        value = row.get('reviewed_' + name, '').strip()
        if value:
            metadata[name] = None if name == 'doi' and value == 'null' else value
    if row.get('reviewed_year', '').strip():
        metadata['year'] = int(row['reviewed_year'])
    if row.get('reviewed_authors_json', '').strip():
        authors = json.loads(row['reviewed_authors_json'])
        if not isinstance(authors, list) or not all(isinstance(author, str) and author.strip() for author in authors):
            raise ValueError('reviewed_authors_json requires a JSON array of nonempty names')
        metadata['authors'] = authors
    return metadata


def validate_reviews(dataset, rows):
    candidates = {paper['candidate_id']: paper for paper in dataset['papers']}
    scope = dataset['scope']
    types = set(scope['core_publication_types'] + scope['extended_publication_types'] + scope['excluded_publication_types'])
    reviewed = deepcopy(candidates)
    seen = set()
    groups = {}
    for row in rows:
        candidate_id = row.get('candidate_id')
        if candidate_id not in candidates or candidate_id in seen:
            raise ValueError('Unknown or repeated candidate_id: {}'.format(candidate_id))
        seen.add(candidate_id)
        paper = reviewed[candidate_id]
        if row.get('professor_id') != paper['professor_id']:
            raise ValueError('Professor identity cannot be changed in the review table')
        if row.get('title') != paper['title'] or json.loads(row.get('authors_json', '[]')) != paper['authors']:
            raise ValueError('Original title/authors are immutable; use reviewed metadata columns for corrections')
        for field in ('year', 'doi', 'publication_type', 'venue'):
            expected = '' if paper.get(field) is None else str(paper[field])
            if row.get(field, expected) != expected:
                raise ValueError('Original {} is immutable; use reviewed metadata columns'.format(field))
        status = (row.get('review_status') or 'candidate').strip().lower()
        ownership = (row.get('ownership') or 'uncertain').strip().lower()
        inclusion = (row.get('inclusion_decision') or 'pending').strip().lower()
        if status not in ('candidate', 'needs_review', 'confirmed'):
            raise ValueError('Review status must be candidate, needs_review or confirmed')
        if status == 'candidate':
            if ownership != 'uncertain' or inclusion != 'pending':
                raise ValueError('Unreviewed candidates must remain uncertain/pending')
            continue
        reviewer = row.get('reviewer', '').strip()
        if not reviewer:
            raise ValueError('A human reviewer is required')
        _date(row.get('reviewed_at', ''))
        evidence = _evidence(row.get('evidence_urls_json', ''))
        if not evidence:
            raise ValueError('Human review needs source evidence')
        metadata = _review_metadata(paper, row)
        if status == 'needs_review':
            if ownership != 'uncertain' or inclusion not in ('pending', 'uncertain'):
                raise ValueError('Unresolved review cannot assign a settled ownership/inclusion label')
        else:
            if ownership not in ('belongs', 'not_belongs') or inclusion not in ('include', 'exclude'):
                raise ValueError('Confirmed review requires explicit ownership and inclusion decisions')
            if any(row.get(check, '').strip().lower() not in TRUE_VALUES for check in CHECKS):
                raise ValueError('Identity, metadata, type and version must all be explicitly checked by a human')
            if len(evidence) < 2:
                raise ValueError('Confirmed review requires at least two distinct evidence URLs')
            if metadata['publication_type'] not in types:
                raise ValueError('Confirmed publication type must follow the dataset taxonomy')
            group_id = row.get('canonical_group_id', '').strip()
            if not group_id:
                raise ValueError('Confirmed review requires an explicit canonical publication group')
            key = (paper['professor_id'], group_id)
            signature = (ownership, metadata['publication_type'], metadata['year'])
            if key in groups and groups[key] != signature:
                raise ValueError('One canonical group cannot merge conflicting ownership, types or publication years')
            groups[key] = signature
            if inclusion == 'include':
                year = metadata['year']
                if ownership != 'belongs' or metadata['publication_type'] not in scope['core_publication_types']:
                    raise ValueError('Core inclusion requires target ownership and a core publication type')
                if not isinstance(year, int) or not scope['year_start'] <= year <= scope['year_end'] or not metadata['authors']:
                    raise ValueError('Core inclusion requires an in-scope year and a checked author list')
        paper.update(metadata, review_status=status, ownership=ownership, inclusion_decision=inclusion)
        year = paper['year']
        paper['in_scope'] = isinstance(year, int) and scope['year_start'] <= year <= scope['year_end']
        paper['annotation_status'] = 'human_reviewed' if status == 'confirmed' else 'human_reviewed_uncertain'
        paper['annotation_evidence'] = evidence
        paper['human_review'] = {name: row.get(name, '') for name in REVIEW_COLUMNS
                                 if name not in ('review_status', 'ownership', 'inclusion_decision')}
    values = list(reviewed.values())
    counts = Counter(paper['review_status'] for paper in values)
    return {'dataset_schema_version': 1, 'dataset_id': dataset['dataset_id'], 'scope': scope,
            'annotation_state': 'partially_human_reviewed' if counts.get('confirmed') else 'candidate_collection',
            'complete_positive_list': False, 'papers': values, 'review_status_counts': dict(counts),
            'note': 'Mechanical annotation validation only; it cannot prove reviewers actually checked sources. '
                    'Canonical groups, excluded types and unresolved entries still need scope-aware final curation. '
                    'No Precision/Recall is computed.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidates', type=Path, required=True)
    parser.add_argument('--annotations', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.resolve() in {args.candidates.resolve(), args.annotations.resolve()}:
        parser.error('Annotations must be saved separately from original candidate data')
    try:
        with args.annotations.open(encoding='utf-8-sig', newline='') as handle:
            rows = list(csv.DictReader(handle))
        result = validate_reviews(read_json(args.candidates), rows)
        write_json(args.output, result)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print('Review states: {}; complete_positive_list=false; no metrics computed'.format(result['review_status_counts']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

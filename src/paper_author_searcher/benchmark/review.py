"""Spreadsheet round trip for human adjudication of pending truth files."""

import csv
import hashlib
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

from paper_author_searcher.evaluation_matching import LABELS, GroundTruth, GroundTruthMatcher
from paper_author_searcher.matching import canonical_doi, normalize

from .truth_sources import clean_arxiv, clean_doi, parse_year

SUGGESTION_FLAG = 'suggested_by_system_not_in_truth'
# Reviews by people certify truth; an assistant's review is recorded separately and never certifies a release.
REVIEW_STATES = ('human_reviewed', 'assistant_reviewed')
SAME_AS = 'same_as:'
STATUS_ORDER = {'verified': 0, 'needs_review': 1, 'rejected': 2}
COLUMNS = (
    'row_kind',
    'evaluation_id',
    'decision',
    'proposed_ownership',
    'review_flags',
    'year',
    'title',
    'authors',
    'venue',
    'dois',
    'external_ids',
    'work_types',
    'truth_sources',
    'evidence',
    'reviewer_note',
)
LIST_SEPARATOR = '; '


def system_suggestions(
    collection: dict, truth: dict, window, run_id: str, limit: int, statuses=('verified',)
) -> List[dict]:
    """Papers the search system collected in the window that match no truth entry: leads for completing the list.

    They are review aids only; a lead enters truth solely when the reviewer confirms it from an independent source.
    """
    start, end = window
    matcher = GroundTruthMatcher(GroundTruth.from_dict(dict(truth, complete_positive_list=False)))
    inner = collection.get('collection', collection)
    seen, rows = set(), []
    for paper in inner.get('all_papers', inner.get('papers')) or []:
        records = paper.get('records') or [paper]
        if paper.get('year') is None or not start <= paper['year'] <= end:
            continue
        # By default only verified output: it is what precision is scored on, so it is what needs a label.
        if statuses and paper.get('ownership_status', 'needs_review') not in statuses:
            continue
        if any(matcher.match(record).decision != 'unmatched' for record in records):
            continue
        key = canonical_doi(paper['doi']) if paper.get('doi') else normalize(paper.get('title') or '')
        if not key or key in seen:
            continue
        seen.add(key)
        external = {}
        for record in records:
            for namespace, value in (record.get('external_ids') or {}).items():
                if namespace.lower() == 'arxiv' and value:
                    external.setdefault('arxiv', str(value))
        status = paper.get('ownership_status', 'needs_review')
        rows.append(
            {
                'row_kind': 'system_suggestion',
                'evaluation_id': '',
                'decision': '',
                'proposed_ownership': '',
                'review_flags': LIST_SEPARATOR.join([SUGGESTION_FLAG, 'system_ownership=' + status]),
                'year': paper.get('year') or '',
                'title': paper.get('title') or '',
                'authors': LIST_SEPARATOR.join(paper.get('authors') or []),
                'venue': paper.get('venue') or '',
                'dois': clean_doi(paper.get('doi')) or '',
                'external_ids': LIST_SEPARATOR.join('{}={}'.format(*item) for item in sorted(external.items())),
                'work_types': paper.get('paper_type') or '',
                'truth_sources': 'system_run:' + run_id,
                'evidence': LIST_SEPARATOR.join(
                    sorted({'{}:{}'.format(record.get('source'), record.get('source_paper_id')) for record in records})
                ),
                'reviewer_note': '',
                '_order': (STATUS_ORDER.get(status, 3), -(paper.get('year') or 0)),
            }
        )
    rows.sort(key=lambda row: row.pop('_order'))
    return rows[:limit]


def export_review(truth: dict, path: Path, suggestions: Optional[List[dict]] = None) -> int:
    with Path(path).open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, COLUMNS)
        writer.writeheader()
        for paper in truth['papers']:
            decided = paper['ownership'] if paper.get('annotation_status') == 'human_reviewed' else ''
            writer.writerow(
                {
                    'row_kind': 'truth',
                    'evaluation_id': paper['evaluation_id'],
                    'decision': decided,
                    'proposed_ownership': paper.get('proposed_ownership', ''),
                    'review_flags': LIST_SEPARATOR.join(paper.get('review_flags') or []),
                    'year': paper.get('year') or '',
                    'title': paper.get('title') or '',
                    'authors': LIST_SEPARATOR.join(paper.get('authors') or []),
                    'venue': paper.get('venue') or paper.get('listed_venue') or '',
                    'dois': LIST_SEPARATOR.join(paper.get('dois') or []),
                    'external_ids': LIST_SEPARATOR.join(
                        '{}={}'.format(*item) for item in sorted((paper.get('external_ids') or {}).items())
                    ),
                    'work_types': LIST_SEPARATOR.join(paper.get('work_types') or []),
                    'truth_sources': LIST_SEPARATOR.join(paper.get('truth_sources') or []),
                    'evidence': LIST_SEPARATOR.join(paper.get('annotation_evidence') or []),
                    'reviewer_note': paper.get('reviewer_note', ''),
                }
            )
        for row in suggestions or []:
            writer.writerow(row)
    return len(truth['papers']) + len(suggestions or [])


def _split(value: str) -> List[str]:
    return [item.strip() for item in (value or '').split(LIST_SEPARATOR.strip()) if item.strip()]


def _added_entry(person_id: str, row: Dict[str, str], decision: str = 'belongs') -> dict:
    """A paper the reviewer found that no source listed: the main reason the list can be called complete."""
    title = (row.get('title') or '').strip()
    if not title:
        raise ValueError('Added review rows need a title')
    note = (row.get('reviewer_note') or '').strip()
    suggested = SUGGESTION_FLAG in (row.get('review_flags') or '')
    if suggested and decision == 'belongs' and not note:
        # Keeps truth independent of the system: a lead counts only with the reviewer's own evidence.
        raise ValueError(f'System suggestion "{title}" needs a reviewer_note naming the independent evidence')
    entry = {
        'title': title,
        'year': parse_year(row.get('year')),
        'authors': _split(row.get('authors', '')),
        'listed_venue': row.get('venue') or None,
        'truth_sources': ['human_reviewer'] + (['confirmed_system_suggestion'] if suggested else []),
        'annotation_evidence': [('Confirmed system suggestion: ' if suggested else 'Added by reviewer: ') + note],
        'review_flags': ['added_by_reviewer'] + ([SUGGESTION_FLAG] if suggested else []),
    }
    dois = [clean_doi(value) for value in _split(row.get('dois', ''))]
    if any(dois):
        entry['dois'] = [doi for doi in dois if doi]
    external = {}
    for item in _split(row.get('external_ids', '')):
        namespace, _, value = item.partition('=')
        if namespace.strip().lower() == 'arxiv':
            value = clean_arxiv(value) or value
        if value.strip():
            external[namespace.strip().lower()] = value.strip()
    if external:
        entry['external_ids'] = external
    seed = '|'.join([title.casefold(), str(entry['year']), *entry.get('dois', [])])
    entry['evaluation_id'] = '{}:added-{}'.format(person_id, hashlib.sha256(seed.encode('utf-8')).hexdigest()[:10])
    return entry


def _merge_alias(target: dict, row: Dict[str, str]) -> None:
    """A lead that is another version of an existing publication: keep its identifiers on that entry."""
    dois = [doi for doi in (clean_doi(value) for value in _split(row.get('dois', ''))) if doi]
    if dois:
        target['dois'] = sorted(set(target.get('dois') or []) | set(dois))
    for item in _split(row.get('external_ids', '')):
        namespace, _, value = item.partition('=')
        if namespace.strip().lower() == 'arxiv' and value.strip():
            target.setdefault('external_ids', {}).setdefault('arxiv', clean_arxiv(value) or value.strip())
    year, authors = parse_year(row.get('year')), _split(row.get('authors', ''))
    if (row.get('title') or '').strip() and year and authors:
        target.setdefault('metadata_aliases', []).append(
            {'title': row['title'].strip(), 'year': year, 'authors': authors}
        )
    target.setdefault('review_flags', []).append('version_identifiers_added_in_review')


def import_review(
    truth: dict,
    path: Path,
    reviewer: str,
    complete_positive_list: bool,
    reviewed_at: Optional[str] = None,
    allow_pending: bool = False,
    review_state: str = 'human_reviewed',
) -> dict:
    if not reviewer.strip():
        raise ValueError('A reviewer name is required')
    if review_state not in REVIEW_STATES:
        raise ValueError(f'review_state must be one of {REVIEW_STATES}')
    reviewed = deepcopy(truth)
    by_id = {paper['evaluation_id']: paper for paper in reviewed['papers']}
    person_id = reviewed['metadata']['person_id']
    seen = set()
    with Path(path).open(encoding='utf-8-sig', newline='') as handle:
        for row in csv.DictReader(handle):
            decision = (row.get('decision') or '').strip()
            if decision.lower().startswith(SAME_AS):
                target = decision[len(SAME_AS) :].strip()
                if target not in by_id:
                    raise ValueError('same_as names an unknown evaluation_id: ' + target)
                _merge_alias(by_id[target], row)
                continue
            decision = decision.lower()
            if decision and decision not in LABELS:
                raise ValueError(f'Decision must be one of {sorted(LABELS)} or same_as:<evaluation_id>: {decision}')
            evaluation_id = (row.get('evaluation_id') or '').strip()
            if not evaluation_id:
                if not decision:
                    continue
                # An 'uncertain' lead stays as an unscored entry so it is neither a hit nor a false positive.
                paper = _added_entry(person_id, row, decision)
                reviewed['papers'].append(paper)
                by_id[paper['evaluation_id']] = paper
                evaluation_id = paper['evaluation_id']
            elif evaluation_id not in by_id:
                raise ValueError('Unknown evaluation_id in review sheet: ' + evaluation_id)
            seen.add(evaluation_id)
            if not decision:
                continue
            paper = by_id[evaluation_id]
            paper['ownership'] = decision
            paper['proposed_ownership'] = decision
            paper['annotation_status'] = review_state
            if (row.get('reviewer_note') or '').strip():
                paper['reviewer_note'] = row['reviewer_note'].strip()
    pending = [paper['evaluation_id'] for paper in reviewed['papers'] if paper.get('annotation_status') != review_state]
    if pending and not allow_pending:
        raise ValueError(f'{len(pending)} entries still need a decision, e.g. {pending[:3]}')
    complete = not pending
    reviewed['metadata'].update(
        {
            'annotation_state': review_state if complete else 'pending_human_review',
            'reviewer': reviewer.strip() if complete else '',
            'reviewed_at': (reviewed_at or date.today().isoformat()) if complete else '',
            'review_progress': {'decided': len(reviewed['papers']) - len(pending), 'pending': len(pending)},
        }
    )
    for paper in reviewed['papers']:
        if paper.get('annotation_status') != review_state:
            paper['ownership'] = 'uncertain'
    # Only a reviewer who searched beyond the listed sources may assert a closed list.
    reviewed['complete_positive_list'] = bool(complete_positive_list and complete)
    GroundTruth.from_dict(reviewed)
    return reviewed

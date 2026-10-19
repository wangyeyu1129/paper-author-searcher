"""Build pending review files from independent reference observations, offline."""

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from copy import deepcopy
from pathlib import Path

REVIEW_COLUMNS = (
    'review_status',
    'ownership',
    'inclusion_decision',
    'canonical_group_id',
    'reviewed_publication_type',
    'reviewed_title',
    'reviewed_authors_json',
    'reviewed_year',
    'reviewed_doi',
    'reviewed_venue',
    'reviewer',
    'reviewed_at',
    'evidence_urls_json',
    'identity_checked',
    'metadata_checked',
    'type_checked',
    'version_checked',
    'review_notes',
)
FACT_COLUMNS = (
    'candidate_id',
    'professor_id',
    'professor_name',
    'title',
    'authors_json',
    'year',
    'doi',
    'venue',
    'publication_type',
    'in_scope',
    'source_urls_json',
    'review_warnings_json',
)


def read_json(path):
    with Path(path).open(encoding='utf-8') as handle:
        return json.load(handle)


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ValueError(f'Refusing to replace an existing dataset file: {path}')
    with path.open('x', encoding='utf-8') as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write('\n')


def _identity(reference):
    extra = reference.get('extra') or {}
    if extra.get('publication_identity_url'):
        return 'reference_url:' + extra['publication_identity_url']
    return 'bibliography_entry:' + reference['reference_id']


def _type_hint(metadata):
    title = (metadata.get('title') or '').strip().lower()
    if title in ('preface', 'editorial', 'foreword'):
        return 'editorial'
    return metadata.get('publication_type') or 'unknown'


def _candidate(reference, professor, scope):
    metadata = deepcopy(reference['metadata'])
    identity = _identity(reference)
    digest = hashlib.sha256(identity.encode('utf-8')).hexdigest()[:20]
    candidate_id = professor['professor_id'] + '_' + digest
    year = metadata.get('year')
    in_scope = isinstance(year, int) and scope['year_start'] <= year <= scope['year_end']
    warnings = list(professor.get('identity_risks') or [])
    if not metadata.get('doi'):
        warnings.append('DOI not reported in the available reference; absence is not proof that no DOI exists.')
    if not metadata.get('authors'):
        warnings.append('Author byline is not provided by the reference; no names are inferred.')
    if not in_scope:
        warnings.append('Related version/artifact outside the declared publication-year scope or with unknown year.')
    extra = reference.get('extra') or {}
    source_records = []
    identity_url = extra.get('publication_identity_url')
    if identity_url:
        source_records.append({'source': 'reference_url', 'paper_id': identity_url})
    return {
        'candidate_id': candidate_id,
        'evaluation_id': candidate_id,
        'professor_id': professor['professor_id'],
        'professor_name': professor['name'],
        'title': metadata['title'],
        'authors': metadata.get('authors') or [],
        'year': year,
        'doi': metadata.get('doi'),
        'venue': metadata.get('venue'),
        'publication_type': _type_hint(metadata),
        'doi_status': 'reported_by_reference' if metadata.get('doi') else 'not_reported_by_available_reference',
        'in_scope': in_scope,
        'source_records': source_records,
        'external_ids': deepcopy(extra.get('external_ids') or {}),
        'source_urls': [reference['source_url']] + ([identity_url] if identity_url else []),
        'reference_evidence': [deepcopy(reference)],
        'review_warnings': warnings,
        'review_status': 'candidate',
        'ownership': 'uncertain',
        'inclusion_decision': 'pending',
        'annotation_status': 'pending_human_review',
        'annotation_evidence': [],
        'metadata_aliases': [],
        'human_review': {
            name: '' for name in REVIEW_COLUMNS if name not in ('review_status', 'ownership', 'inclusion_decision')
        },
    }


def build_candidates(scope, references):
    if references.get('source_scope') != 'independent_official_references':
        raise ValueError('An independent reference snapshot is required; retrieval output is not accepted')
    professors = {professor['professor_id']: professor for professor in scope['professors']}
    grouped = {}
    for reference in references['records']:
        if not reference.get('source_url') or not reference.get('collection_method'):
            raise ValueError('Reference source URL and collection method are required')
        if not reference.get('metadata', {}).get('title'):
            raise ValueError('Reference must provide an observed title')
        professor = professors[reference['professor_id']]
        key = (professor['professor_id'], _identity(reference))
        if key not in grouped:
            grouped[key] = _candidate(reference, professor, scope)
            continue
        candidate = grouped[key]
        candidate['reference_evidence'].append(deepcopy(reference))
        candidate['source_urls'] = sorted(set(candidate['source_urls'] + [reference['source_url']]))
        observed = reference['metadata']
        for field in ('title', 'authors', 'year', 'doi', 'venue'):
            if candidate.get(field) != observed.get(field):
                candidate['review_warnings'].append(f'Same explicit reference URL has differing {field} descriptions.')
        # Evidence grouping is not a human identity/version decision. Labels stay pending.
    candidates = sorted(
        grouped.values(),
        key=lambda item: (item['professor_id'], item['year'] or 0, item['title'], item['candidate_id']),
    )
    return {
        'dataset_schema_version': 1,
        'dataset_id': scope['dataset_id'],
        'scope': deepcopy(scope),
        'annotation_state': 'candidate_collection',
        'human_confirmed_count': 0,
        'complete_positive_list': False,
        'papers': candidates,
        'statistics': {
            'candidate_count': len(candidates),
            'by_professor': dict(Counter(paper['professor_id'] for paper in candidates)),
            'by_publication_type': dict(Counter(paper['publication_type'] for paper in candidates)),
            'with_reported_doi': sum(bool(paper['doi']) for paper in candidates),
        },
    }


def export_candidates(dataset, output_directory):
    root = Path(output_directory)
    root.mkdir(parents=True, exist_ok=True)
    outputs = [root / 'candidates.json', root / 'candidates.csv']
    for professor in dataset['scope']['professors']:
        if not re.fullmatch(r'[a-z0-9_]+', professor['professor_id']):
            raise ValueError('Professor dataset ID must be a safe lowercase identifier')
        outputs.append(root / 'professors' / (professor['professor_id'] + '.pending.json'))
    if any(path.exists() for path in outputs):
        raise ValueError('Use a new output directory; pending/review files must not be overwritten')
    write_json(outputs[0], dataset)
    with outputs[1].open('x', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=FACT_COLUMNS + REVIEW_COLUMNS)
        writer.writeheader()
        for paper in dataset['papers']:
            row = {name: paper.get(name, '') for name in FACT_COLUMNS + REVIEW_COLUMNS}
            row.update(paper['human_review'])
            row['authors_json'] = json.dumps(paper['authors'], ensure_ascii=False)
            row['source_urls_json'] = json.dumps(paper['source_urls'], ensure_ascii=False)
            row['review_warnings_json'] = json.dumps(paper['review_warnings'], ensure_ascii=False)
            writer.writerow(row)
    for professor in dataset['scope']['professors']:
        papers = [paper for paper in dataset['papers'] if paper['professor_id'] == professor['professor_id']]
        pending = {
            'schema_version': 1,
            'professor': {
                'name': professor['name'],
                'university': professor['university'],
                'author_ids': {},
                'author_ids_complete': False,
                'reference_identity': professor,
            },
            'complete_positive_list': False,
            'metadata': {
                'annotation_state': 'candidate_collection',
                'reviewer': None,
                'reviewed_at': None,
                'scope': dataset['scope'],
                'dataset_id': dataset['dataset_id'],
                'warning': 'Candidates and source claims only. No human annotation or formal Recall available.',
            },
            'papers': papers,
        }
        write_json(root / 'professors' / (professor['professor_id'] + '.pending.json'), pending)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scope', type=Path, required=True)
    parser.add_argument('--references', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        dataset = build_candidates(read_json(args.scope), read_json(args.references))
        export_candidates(dataset, args.output_dir)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print('Candidates: {}; human confirmed: 0'.format(dataset['statistics']['candidate_count']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

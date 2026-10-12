"""Append independently supported reference records without inventing human labels."""
import argparse
from copy import deepcopy
from pathlib import Path

from paper_author_searcher.evaluation_matching import stable_identifiers
from scripts.ground_truth.build_candidates import build_candidates, export_candidates, read_json, write_json


def prepare_revision(base, sources):
    if any(paper['review_status'] != 'candidate' for paper in base['papers']):
        raise ValueError('Revision preparation cannot reset existing human decisions')
    if sources.get('source_scope') != 'independent_official_references':
        raise ValueError('Revision requires independent official references')
    dataset_id = sources.get('next_dataset_id')
    if not dataset_id or dataset_id == base['dataset_id']:
        raise ValueError('Revision must declare a new dataset_id')
    scope = deepcopy(base['scope'])
    scope['dataset_id'] = dataset_id
    additions = []
    existing_ids = {paper['candidate_id'] for paper in base['papers']}
    existing_identifiers = set().union(*(stable_identifiers(paper) for paper in base['papers']))
    omission_links = []
    for entry in sources.get('additions', []):
        primary = entry['primary_reference']
        metadata = primary['metadata']
        if not primary.get('extra', {}).get('publication_identity_url'):
            raise ValueError('New publication requires an observed official publication URL')
        if not metadata.get('authors') or not isinstance(metadata.get('year'), int) or isinstance(metadata['year'], bool):
            raise ValueError('New publication needs an independently observed complete author list and year')
        if not entry.get('supporting_references'):
            raise ValueError('New publication needs independent supporting bibliography evidence')
        if metadata['publication_type'] not in scope['core_publication_types']:
            raise ValueError('Missing core-publication revision cannot hide omissions as non-core types')
        if not scope['year_start'] <= metadata['year'] <= scope['year_end']:
            raise ValueError('New reference is outside the declared publication-year scope')
        candidate = build_candidates(scope, {'source_scope': 'independent_official_references', 'records': [primary]})['papers'][0]
        if candidate['candidate_id'] in existing_ids or stable_identifiers(candidate) & existing_identifiers:
            raise ValueError('New publication identifier already exists; review duplicate/version conflict')
        for support in entry.get('supporting_references', []):
            if not support.get('source_url') or not support.get('collection_method'):
                raise ValueError('Supporting bibliography evidence requires provenance')
            candidate['reference_evidence'].append(deepcopy(support))
            candidate['source_urls'].append(support['source_url'])
        candidate['source_urls'] = sorted(set(candidate['source_urls']))
        candidate['review_warnings'].extend(entry.get('review_questions', []))
        candidate['revision_provenance'] = {'omission_id': entry['omission_id'],
                                          'support_state': 'independently_supported_candidate_not_human_confirmed'}
        additions.append(candidate)
        existing_ids.add(candidate['candidate_id'])
        existing_identifiers.update(stable_identifiers(candidate))
        omission_links.append({'omission_id': entry['omission_id'], 'new_candidate_id': candidate['candidate_id'],
                               'review_status': 'candidate', 'human_completeness_decision': 'pending'})
    revised = deepcopy(base)
    revised.update(dataset_id=dataset_id, scope=scope, papers=deepcopy(base['papers']) + additions,
                   previous_dataset_id=base['dataset_id'], annotation_state='candidate_collection',
                   human_confirmed_count=0, complete_positive_list=False,
                   revision={'original_candidate_count': len(base['papers']), 'added_candidate_count': len(additions),
                             'omission_links': omission_links, 'reference_independence': sources['source_scope']},
                   statistics={'candidate_count': len(base['papers']) + len(additions)})
    requirements = revised.setdefault('review_requirements', {})
    requirements['supported_in_scope_omissions'] = {item['omission_id']: item['new_candidate_id'] for item in omission_links}
    requirements['source_ids'] = sorted(set(requirements.get('source_ids', [])) |
                                         {entry['primary_reference']['source_id'] for entry in sources.get('additions', [])})
    return revised


def revision_coverage(previous, revised, sources):
    coverage = deepcopy(previous)
    links = {item['omission_id']: item['new_candidate_id'] for item in revised['revision']['omission_links']}
    for omission in coverage['omissions']:
        if omission['omission_id'] in links:
            omission['next_revision_candidate_id'] = links[omission['omission_id']]
            # Merely adding a candidate does not constitute a human completeness verdict.
            omission.update(decision='pending', covered_candidate_id='', human_notes='')
    existing = {item['source_id'] for item in coverage['sources']}
    for addition in sources['additions']:
        primary = addition['primary_reference']
        if primary['source_id'] not in existing:
            coverage['sources'].append({'source_id': primary['source_id'], 'url': primary['source_url'],
                                        'observed_fetch_status': 'independent_metadata_observed',
                                        'human_checked': False, 'notes': ''})
            existing.add(primary['source_id'])
    coverage.update(approved=False, reviewer='', reviewed_at='', evidence_urls=[],
                    additional_venue_audit_complete=False, identity_conflicts_resolved=False)
    for item in coverage['sources']:
        item.update(human_checked=False, notes='')
    return coverage


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--sources', type=Path, required=True)
    parser.add_argument('--coverage-template', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        sources = read_json(args.sources)
        revised = prepare_revision(read_json(args.base), sources)
        export_candidates(revised, args.output_dir)
        write_json(args.output_dir / 'coverage_review.template.json',
                   revision_coverage(read_json(args.coverage_template), revised, sources))
        write_json(args.output_dir / 'revision_provenance.json', revised['revision'])
    except (OSError, KeyError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print('Prepared next revision: {} original + {} new candidates; confirmed=0'.format(
        revised['revision']['original_candidate_count'], revised['revision']['added_candidate_count']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

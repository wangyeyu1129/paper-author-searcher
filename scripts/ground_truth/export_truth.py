"""Convert explicit human annotations into version-aware Evaluation Ground Truth."""

import argparse
import csv
import json
from collections import defaultdict
from copy import deepcopy
from pathlib import Path

from scripts.ground_truth.build_candidates import read_json, write_json
from scripts.ground_truth.review_candidates import _date, _evidence, validate_reviews

from paper_author_searcher.evaluation_matching import GroundTruth
from paper_author_searcher.matching import canonical_doi


def human_attestation(value):
    if not isinstance(value.get('reviewer'), str) or not value['reviewer'].strip():
        raise ValueError('Human reviewer is required')
    _date(value.get('reviewed_at'))
    urls = _evidence(json.dumps(value.get('evidence_urls', [])))
    if len(urls) < 2:
        raise ValueError('Human attestation requires two distinct evidence URLs')


def completeness_reasons(reviewed, coverage, requirements=None):
    reasons = []
    if any(paper['review_status'] != 'confirmed' for paper in reviewed['papers']):
        reasons.append('Candidate review is unfinished')
    if coverage.get('approved') is not True:
        reasons.append('Human completeness approval is pending')
    if not coverage.get('sources') or any(source.get('human_checked') is not True for source in coverage['sources']):
        reasons.append('Independent source audit is unfinished')
    requirements = requirements or {}
    actual_sources = {source['source_id'] for source in coverage.get('sources', [])}
    if not set(requirements.get('source_ids', [])).issubset(actual_sources):
        reasons.append('Required independent source checks were omitted')
    actual_omissions = {item['omission_id'] for item in coverage.get('omissions', [])}
    if not set(requirements.get('omission_ids', [])).issubset(actual_omissions):
        reasons.append('Known completeness findings were omitted')
    for check in ('additional_venue_audit_complete', 'identity_conflicts_resolved'):
        if coverage.get(check) is not True:
            reasons.append(check + ' is pending')
    by_id = {}
    for paper in reviewed['papers']:
        by_id[paper['candidate_id']] = paper
        for alias in paper.get('candidate_ids', []):
            by_id[alias] = paper
    included_groups = {
        paper['human_review'].get('canonical_group_id')
        for paper in reviewed['papers']
        if paper.get('inclusion_decision') == 'include'
    }
    scope = reviewed.get('scope')
    if scope:
        for paper in reviewed['papers']:
            excluded_positive = (
                paper.get('review_status') == 'confirmed'
                and paper['ownership'] == 'belongs'
                and paper['in_scope']
                and paper['publication_type'] in scope['core_publication_types']
                and paper['inclusion_decision'] == 'exclude'
            )
            if excluded_positive and paper['human_review'].get('canonical_group_id') not in included_groups:
                reasons.append('An in-scope owned core publication was excluded without a retained canonical group')
    for omission in coverage.get('omissions', []):
        decision = omission.get('decision')
        supported = requirements.get('supported_in_scope_omissions', {}).get(omission['omission_id'])
        if supported and decision == 'out_of_scope':
            reasons.append('An independently supported in-scope omission cannot be hidden as out_of_scope')
            continue
        if decision == 'already_covered':
            paper = by_id.get(omission.get('covered_candidate_id'))
            if not paper or paper['inclusion_decision'] != 'include' or not omission.get('human_notes', '').strip():
                reasons.append('Omission coverage needs a confirmed positive and explanatory evidence')
            elif supported and paper['candidate_id'] != supported and supported not in paper.get('candidate_ids', []):
                reasons.append('A supported omission must link to its actual new reference identity')
        elif decision == 'not_target_author':
            paper = by_id.get(omission.get('covered_candidate_id'))
            if (
                not paper
                or paper['review_status'] != 'confirmed'
                or paper['ownership'] != 'not_belongs'
                or (
                    supported and paper['candidate_id'] != supported and supported not in paper.get('candidate_ids', [])
                )
                or not omission.get('human_notes', '').strip()
            ):
                reasons.append('A not-target-author omission requires an explicitly adjudicated negative identity')
        elif decision != 'out_of_scope' or not omission.get('human_notes', '').strip():
            reasons.append('Possible missing publication is unresolved')
    return reasons


def _canonical_paper(group_id, members):
    selected = deepcopy(members[0])
    for field in ('ownership', 'publication_type', 'year'):
        if len({paper.get(field) for paper in members}) != 1:
            raise ValueError('Canonical group conflicts in ' + field)
    dois = {canonical_doi(paper['doi']) for paper in members if paper.get('doi')}
    if len(dois) > 1:
        raise ValueError('Conflicting DOI/version records cannot share a canonical group')
    selected['evaluation_id'] = selected['professor_id'] + ':' + group_id
    selected['candidate_ids'] = [paper['candidate_id'] for paper in members]
    selected['doi'] = next(iter(dois), None)
    selected['source_records'] = []
    selected['reference_evidence'] = []
    selected['annotation_evidence'] = []
    selected['metadata_aliases'] = []
    selected['external_ids'] = {}
    for member in members:
        selected['source_records'].extend(deepcopy(member.get('source_records') or []))
        selected['reference_evidence'].extend(deepcopy(member.get('reference_evidence') or []))
        selected['annotation_evidence'].extend(member['annotation_evidence'])
        for namespace, identifier in (member.get('external_ids') or {}).items():
            existing = selected['external_ids'].get(namespace)
            if existing and existing != identifier:
                raise ValueError('Conflicting external identifiers need separate publications')
            selected['external_ids'][namespace] = identifier
        # Each member's corrected metadata was explicitly checked in the human CSV.
        if member != members[0]:
            selected['metadata_aliases'].append(
                {key: deepcopy(member.get(key)) for key in ('title', 'authors', 'year', 'venue')}
            )
    selected['source_records'] = list(
        {(item['source'], item['paper_id']): item for item in selected['source_records']}.values()
    )
    selected['annotation_evidence'] = sorted(set(selected['annotation_evidence']))
    return selected


def _matching_annotations(truth, reviewed, annotations):
    by_id = {paper['evaluation_id']: paper for paper in truth['papers']}
    candidates = {paper['candidate_id']: paper for paper in reviewed['papers']}
    for item in annotations.get('metadata_aliases', []):
        human_attestation(item)
        target = by_id.get(item.get('evaluation_id'))
        alias = item.get('metadata')
        if not target or not isinstance(alias, dict):
            raise ValueError('Metadata alias requires an existing evaluation_id and metadata')
        if not alias.get('title') or not alias.get('authors') or not isinstance(alias.get('year'), int):
            raise ValueError('Metadata aliases need a checked title, year and complete author list')
        if (
            item.get('same_publication') is not True
            or alias.get('publication_type', target['publication_type']) != target['publication_type']
        ):
            raise ValueError('Metadata aliases require explicit same-publication approval; versions stay separate')
        if alias.get('year') != target['year'] and not item.get('year_difference_reason', '').strip():
            raise ValueError('Publication-year alias needs an explicit human explanation')
        target['metadata_aliases'].append(deepcopy(alias))
        target.setdefault('matching_approval_evidence', []).append(deepcopy(item))
    relations = []
    for item in annotations.get('preprint_relations', []):
        human_attestation(item)
        formal = by_id.get(item.get('formal_evaluation_id'))
        preprint = candidates.get(item.get('preprint_candidate_id'))
        if not formal or formal['ownership'] != 'belongs' or not preprint or preprint['review_status'] != 'confirmed':
            raise ValueError('Preprint relation requires two human-confirmed identities')
        if preprint['publication_type'] != 'preprint' or preprint['ownership'] != 'belongs':
            raise ValueError('Related preprint must belong to the professor and retain its separate type')
        relations.append(dict(deepcopy(item), preprint_metadata=deepcopy(preprint)))
    truth['preprint_relations'] = relations


def approved_relationships(rows, truth, reviewed):
    """Translate only explicitly reviewed relationships; suggestions remain inert."""
    candidates = {paper['candidate_id']: paper for paper in reviewed['papers']}
    formal_by_candidate = {}
    for paper in truth['papers']:
        for candidate_id in paper['candidate_ids']:
            formal_by_candidate[candidate_id] = paper['evaluation_id']
    relations = []
    for row in rows:
        relation = row.get('human_relation', '').strip()
        if not relation:
            continue
        approval = {
            'reviewer': row.get('reviewer'),
            'reviewed_at': row.get('reviewed_at'),
            'evidence_urls': json.loads(row.get('evidence_urls') or '[]'),
        }
        human_attestation(approval)
        ids = json.loads(row['candidate_ids'])
        if len(ids) != 2 or len(set(ids)) != 2 or any(key not in candidates for key in ids):
            raise ValueError('Reviewed relationship must reference two known candidate IDs')
        left, right = [candidates[key] for key in ids]
        if any(paper['review_status'] != 'confirmed' for paper in (left, right)):
            raise ValueError('Both relationship records must be individually human confirmed')
        same_group = left['human_review']['canonical_group_id'] == right['human_review']['canonical_group_id']
        if relation == 'same_publication':
            if not same_group:
                raise ValueError('Same-publication review must agree with canonical group assignments')
        elif relation in ('distinct_publications', 'unrelated'):
            if same_group:
                raise ValueError('Distinct versions cannot share a canonical group')
        elif relation == 'preprint_of':
            preprints = [paper for paper in (left, right) if paper['publication_type'] == 'preprint']
            if len(preprints) != 1 or same_group:
                raise ValueError('Preprint relationship must preserve two separate publication identities')
            preprint = preprints[0]
            formal_id = next(
                (
                    formal_by_candidate[key]
                    for key in ids
                    if key != preprint['candidate_id'] and key in formal_by_candidate
                ),
                None,
            )
            if not formal_id:
                raise ValueError('Preprint relation needs an included formal reference identity')
            relations.append(
                dict(approval, formal_evaluation_id=formal_id, preprint_candidate_id=preprint['candidate_id'])
            )
        else:
            raise ValueError('Human relation must be same_publication, distinct_publications, unrelated or preprint_of')
    return relations


def export_truth(dataset, rows, coverage, annotations=None, relationships=None):
    if len(dataset['scope']['professors']) != 1:
        raise ValueError('Export one professor pilot at a time')
    reviewed = validate_reviews(dataset, rows)
    groups = defaultdict(list)
    excluded = []
    scope = dataset['scope']
    for paper in reviewed['papers']:
        core = paper['in_scope'] and paper['publication_type'] in scope['core_publication_types']
        if paper['review_status'] != 'confirmed' or not core:
            excluded.append(paper)
            continue
        if paper['ownership'] == 'belongs' and paper['inclusion_decision'] != 'include':
            excluded.append(paper)
            continue
        groups[paper['human_review']['canonical_group_id']].append(paper)
    retained_exclusions = []
    for paper in excluded:
        group = paper['human_review'].get('canonical_group_id')
        duplicate = (
            paper['review_status'] == 'confirmed'
            and paper['ownership'] == 'belongs'
            and paper['in_scope']
            and paper['publication_type'] in scope['core_publication_types']
            and group in groups
            and groups[group][0]['ownership'] == 'belongs'
        )
        if duplicate:
            groups[group].append(paper)
        else:
            retained_exclusions.append(paper)
    professor = scope['professors'][0]
    reasons = completeness_reasons(reviewed, coverage, dataset.get('review_requirements'))
    if coverage.get('approved') is True:
        human_attestation(coverage)
        if reasons:
            raise ValueError('Cannot approve completeness: ' + '; '.join(reasons))
    truth = {
        'schema_version': 1,
        'professor': {
            'name': professor['name'],
            'university': professor['university'],
            'author_ids': {},
            'author_ids_complete': False,
        },
        'complete_positive_list': not reasons,
        'metadata': {
            'annotation_state': 'human_reviewed' if not reasons else 'review_incomplete',
            'reviewer': coverage.get('reviewer'),
            'reviewed_at': coverage.get('reviewed_at'),
            'dataset_id': dataset['dataset_id'],
            'scope': deepcopy(scope),
            'formal_evaluation_ready': not reasons,
            'pending_reasons': reasons,
            'review_requirements': deepcopy(dataset.get('review_requirements', {})),
            'coverage_review': deepcopy(coverage),
            'candidate_review_count': len(reviewed['papers']),
        },
        'papers': [_canonical_paper(key, members) for key, members in sorted(groups.items())],
        'excluded_or_pending_records': retained_exclusions,
    }
    matching = deepcopy(annotations or {})
    matching.setdefault('preprint_relations', []).extend(approved_relationships(relationships or [], truth, reviewed))
    _matching_annotations(truth, reviewed, matching)
    GroundTruth.from_dict(truth)
    return truth


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('candidates', 'annotations', 'coverage', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--matching-annotations', type=Path)
    parser.add_argument('--relationships', type=Path)
    args = parser.parse_args(argv)
    try:
        with args.annotations.open(encoding='utf-8-sig', newline='') as handle:
            rows = list(csv.DictReader(handle))
        matching = read_json(args.matching_annotations) if args.matching_annotations else {}
        relationships = []
        if args.relationships:
            with args.relationships.open(encoding='utf-8-sig', newline='') as handle:
                relationships = list(csv.DictReader(handle))
        truth = export_truth(read_json(args.candidates), rows, read_json(args.coverage), matching, relationships)
        write_json(args.output, truth)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(
        'Human publications: {}; formal evaluation ready: {}'.format(
            len(truth['papers']), truth['metadata']['formal_evaluation_ready']
        )
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

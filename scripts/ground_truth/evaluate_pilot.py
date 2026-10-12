"""Offline pilot evaluation gated by human reference and independent result review."""
import argparse
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path

from paper_author_searcher.evaluation_matching import GroundTruth, GroundTruthMatcher, TruthMatch, stable_identifiers
from scripts.ground_truth.build_candidates import read_json, write_json
from scripts.ground_truth.export_truth import completeness_reasons, human_attestation


def fingerprint(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


def match_publication(record, truth, matcher):
    """Preprint links describe relations, not discovery of a formal publication."""
    publication_type = (record.get('publication_type') or '').casefold().replace('_', ' ')
    preprint = record.get('paper_type') == 'preprint' or publication_type == 'preprint' or record.get('source') == 'arxiv'
    keys = stable_identifiers(record)
    core_identifiers = set().union(*(stable_identifiers(paper) for paper in truth['papers']))
    for excluded in truth.get('excluded_or_pending_records', []):
        if excluded.get('publication_type') == 'preprint' and keys & stable_identifiers(excluded) and not keys & core_identifiers:
            preprint = True
    linked = []
    for relation in truth.get('preprint_relations', []):
        if stable_identifiers(record) & stable_identifiers(relation['preprint_metadata']):
            linked.append(relation['formal_evaluation_id'])
    if preprint:
        return TruthMatch(candidates=linked, evidence=['Preprint is separate from a formal core publication'],
                          decision='related_preprint' if linked else 'preprint_outside_core_scope')
    result = matcher.match(record)
    if result.evaluation_id:
        expected = matcher.by_id[result.evaluation_id]
        scope = truth['metadata']['scope']
        explicit_type = record.get('publication_type')
        kind = record.get('paper_type')
        noncore = explicit_type in scope['extended_publication_types'] + scope['excluded_publication_types']
        if noncore or kind in scope['extended_publication_types'] + scope['excluded_publication_types']:
            return TruthMatch(candidates=[result.evaluation_id], evidence=result.evidence + ['Non-core publication/version conflict'],
                              decision='version_conflict')
        formal_types = set(scope['core_publication_types']) | {'article', 'journal-article', 'journalarticle', 'conference', 'conference-paper', 'inproceedings'}
        if result.decision.startswith('metadata') and not record.get('venue') and explicit_type not in formal_types:
            return TruthMatch(candidates=[result.evaluation_id], evidence=result.evidence + ['No publication-level type or venue establishes the formal version'],
                              decision='version_unresolved')
        # DOI can establish a record despite date variation; metadata-only matching uses checked year aliases.
        if record.get('year') != expected.get('year'):
            result.evidence.append('Observed year differs; publication identifier or human-approved year alias used')
    return result


def result_entries(saved):
    collection = saved.get('collection', saved)
    papers = collection.get('all_papers', collection.get('papers'))
    if not isinstance(papers, list) or not all(isinstance(paper, dict) for paper in papers):
        raise ValueError('Saved collection must contain all_papers or papers objects')
    return papers


def prepare_audit(saved, truth):
    matcher = GroundTruthMatcher(GroundTruth.from_dict(truth))
    queue = []
    evidence = []
    for index, paper in enumerate(result_entries(saved)):
        records = paper.get('records') or [paper]
        if not isinstance(records, list) or not all(isinstance(record, dict) for record in records):
            raise ValueError('Original paper records must be objects')
        matches = [asdict(match_publication(record, truth, matcher)) for record in records]
        ids = sorted({match['evaluation_id'] for match in matches if match['evaluation_id']})
        entry_id = '{}:{}'.format(index, fingerprint(paper)[:20])
        evidence.append({'entry_id': entry_id, 'entry_index': index, 'evaluation_ids': ids, 'matches': matches})
        if len(ids) == 1 and all(match['evaluation_id'] == ids[0] for match in matches):
            continue
        # Hide model predictions and scores from the independent human worksheet.
        fields = ('title', 'authors', 'year', 'doi', 'venue', 'publication_type', 'paper_type',
                  'source', 'source_paper_id', 'source_author_id', 'external_ids', 'paper_url')
        queue.append({'entry_id': entry_id, 'entry_index': index,
                      'paper': {key: deepcopy(paper.get(key)) for key in fields},
                      'source_records': [{key: deepcopy(record.get(key)) for key in fields} for record in records],
                      'matching_evidence': matches, 'decision': 'uncertain',
                      'evaluation_id': '', 'canonical_group_id': '', 'reviewer': '',
                      'reviewed_at': '', 'evidence_urls': [], 'notes': ''})
    return {'schema_version': 1, 'result_sha256': fingerprint(saved), 'truth_sha256': fingerprint(truth),
            'review_status': 'pending', 'papers': queue, 'matching_evidence': evidence,
            'note': 'Absence from the reference set is not a false positive. Review ownership and scope independently. '
                    'A newly found true publication absent from Ground Truth blocks formal evaluation.'}


def _audit_decisions(audit, template, truth):
    if audit.get('result_sha256') != template['result_sha256'] or audit.get('truth_sha256') != template['truth_sha256']:
        raise ValueError('Audit is stale or belongs to a different result/reference snapshot')
    expected = {item['entry_id']: item for item in template['papers']}
    rows = audit.get('papers', [])
    if not isinstance(rows, list):
        raise ValueError('Audit papers must be a list')
    decisions = {}
    positives = {paper['evaluation_id'] for paper in truth['papers'] if paper['ownership'] == 'belongs'}
    for row in rows:
        entry_id = row.get('entry_id')
        if entry_id not in expected or entry_id in decisions:
            raise ValueError('Unknown or repeated audit entry_id')
        for field in ('paper', 'source_records', 'matching_evidence', 'entry_index'):
            if row.get(field) != expected[entry_id][field]:
                raise ValueError('Audit source facts and matching evidence are immutable')
        label = row.get('decision', 'uncertain')
        if label not in ('uncertain', 'belongs', 'not_belongs', 'out_of_scope'):
            raise ValueError('Unknown human result-audit decision')
        if label != 'uncertain':
            human_attestation(row)
            if not row.get('notes', '').strip():
                raise ValueError('Human result review requires an explanatory note')
            if label == 'belongs' and row.get('evaluation_id') not in positives:
                raise ValueError('True retrieved publication is missing from Ground Truth; independent revision required')
            if label == 'not_belongs' and not row.get('canonical_group_id', '').strip():
                raise ValueError('Negative publications require an explicit human deduplication group')
            matched = {match['evaluation_id'] for match in row['matching_evidence'] if match['evaluation_id']}
            if len(matched) > 1:
                raise ValueError('Conflicting merged identities require record-level repair before scoring')
            if matched and (label != 'belongs' or row.get('evaluation_id') not in matched):
                raise ValueError('Human result label conflicts with reference identity; adjudicate before scoring')
            if label == 'belongs' and all(match['decision'] in ('related_preprint', 'preprint_outside_core_scope', 'version_conflict')
                                          for match in row['matching_evidence']):
                raise ValueError('Preprint/non-core version cannot be counted as formal publication discovery')
            if label == 'belongs' and any(match['decision'] in ('identifier_conflict', 'ambiguous') for match in row['matching_evidence']):
                raise ValueError('Identifier/version conflict must be adjudicated in reference metadata before scoring')
        decisions[entry_id] = row
    return decisions


def _reference_pending(truth):
    metadata = truth.get('metadata', {})
    reasons = list(metadata.get('pending_reasons') or [])
    if metadata.get('formal_evaluation_ready') is not True or metadata.get('annotation_state') != 'human_reviewed':
        reasons.append('Ground Truth human review/finalization is incomplete')
    if truth.get('complete_positive_list') is not True:
        reasons.append('Independent positive-list completeness has not been approved')
    if any(paper.get('annotation_status') != 'human_reviewed' or paper['ownership'] == 'uncertain' for paper in truth['papers']):
        reasons.append('Unconfirmed reference labels cannot be scored formally')
    if not reasons:
        human_attestation(metadata.get('coverage_review') or {})
        records = truth['papers'] + truth.get('excluded_or_pending_records', [])
        reasons.extend(completeness_reasons({'papers': records, 'scope': metadata['scope']}, metadata.get('coverage_review') or {},
                                           metadata.get('review_requirements')))
    return reasons


def evaluate_pilot(saved, truth, audit=None):
    template = prepare_audit(saved, truth)
    decisions = _audit_decisions(audit, template, truth) if audit is not None else {}
    reasons = _reference_pending(truth)
    unresolved = [item['entry_id'] for item in template['papers']
                  if decisions.get(item['entry_id'], {}).get('decision', 'uncertain') == 'uncertain']
    if unresolved:
        reasons.append('Independent result audit is incomplete')
    report = {'schema_version': 1, 'status': 'blocked_pending_human_review' if reasons else 'ready',
              'pending_reasons': sorted(set(reasons)), 'formal_metrics': None,
              'unresolved_result_entries': unresolved, 'matching_evidence': template['matching_evidence'],
              'result_sha256': template['result_sha256'], 'truth_sha256': template['truth_sha256'],
              'metric_scope': 'unique human-adjudicated formal core publications in the approved year window'}
    if reasons:
        return report
    by_id = {paper['evaluation_id']: paper for paper in truth['papers']}
    positives = {key for key, paper in by_id.items() if paper['ownership'] == 'belongs'}
    found = set()
    verified_positive = set()
    verified_negative = set()
    false_positives = []
    excluded = []
    for paper, entry in zip(result_entries(saved), template['matching_evidence']):
        status = paper.get('ownership_status', 'needs_review')
        if status not in ('verified', 'needs_review', 'rejected'):
            raise ValueError('Unknown ownership_status')
        decision = decisions.get(entry['entry_id'])
        if decision:
            label = decision['decision']
            if label == 'out_of_scope':
                excluded.append(entry['entry_id'])
                continue
            evaluation_id = decision.get('evaluation_id') if label == 'belongs' else 'negative:' + decision['canonical_group_id']
        else:
            evaluation_id = entry['evaluation_ids'][0]
            label = by_id[evaluation_id]['ownership']
        if label == 'belongs':
            found.add(evaluation_id)
            if status == 'verified':
                verified_positive.add(evaluation_id)
        elif label == 'not_belongs' and status == 'verified':
            verified_negative.add(evaluation_id)
            false_positives.append(dict(entry, human_review=deepcopy(decision),
                                        reference_evidence=deepcopy(by_id.get(evaluation_id, {}).get('annotation_evidence', []))))
    tp, fp = len(verified_positive), len(verified_negative)
    fn = len(positives - verified_positive)
    def ratio(numerator, denominator):
        return numerator / denominator if denominator else None
    report['formal_metrics'] = {
        'paper_discovery_recall': ratio(len(found), len(positives)),
        'ownership_precision': ratio(tp, tp + fp), 'ownership_recall': ratio(tp, len(positives)),
        'ownership_f1': ratio(2 * tp, 2 * tp + fp + fn),
        'positive_reference_publications': len(positives), 'discovered_positives': len(found),
        'verified_true_positives': tp, 'verified_false_positives': fp, 'verified_false_negatives': fn}
    report.update(false_positives=false_positives, discovery_false_negatives=sorted(positives - found),
                  ownership_false_negatives=sorted(positives - verified_positive), excluded_entries=excluded)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--result', type=Path, required=True)
    parser.add_argument('--truth', type=Path, required=True)
    parser.add_argument('--audit', type=Path)
    parser.add_argument('--audit-output', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        saved, truth = read_json(args.result), read_json(args.truth)
        if args.audit_output:
            write_json(args.audit_output, prepare_audit(saved, truth))
        report = evaluate_pilot(saved, truth, read_json(args.audit) if args.audit else None)
        write_json(args.output, report)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print('Evaluation: {}; formal metrics {}'.format(report['status'],
                                                     'withheld' if report['formal_metrics'] is None else 'available'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

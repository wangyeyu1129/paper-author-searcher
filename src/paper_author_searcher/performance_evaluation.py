"""Offline stage metrics with explicit denominators and human-annotation scope."""
import hashlib
import json
from collections import Counter
from copy import deepcopy
from pathlib import Path

from .evaluation import Evaluator
from .evaluation_matching import GroundTruth, GroundTruthMatcher, MATCHING_RULES_VERSION
from .performance_diagnostics import ownership_diagnostics, pdf_failure_reasons
from .pdf_downloader import file_digest, paper_key


def rate(numerator, denominator):
    return {'numerator': numerator, 'denominator': denominator,
            'rate': numerator / denominator if denominator else None}


def _papers(saved):
    if saved is None:
        return []
    collection = saved.get('collection', saved)
    return collection.get('all_papers', collection.get('papers')) or []


def _stage_scope(saved):
    if saved is None:
        return None
    if not isinstance(saved, dict) or not isinstance(saved.get('papers'), list):
        raise ValueError('PDF reports and manifests must be objects containing a papers list')
    identities = []
    for paper in saved['papers']:
        if not isinstance(paper, dict):
            raise ValueError('Stage papers must be objects')
        if paper.get('validation_status') not in ('skipped_ownership', 'not_processed'):
            identities.append(paper_key(paper))
    digest = hashlib.sha256(json.dumps(sorted(set(identities))).encode('utf-8')).hexdigest()
    return {'processed_publications': len(set(identities)), 'fingerprint': digest}


def _association(paper, matcher):
    metadata = paper.get('paper_metadata') or paper
    matches = [matcher.match(record) for record in metadata.get('records') or [metadata]]
    identities = {match.evaluation_id for match in matches if match.evaluation_id}
    conflicts = any(match.decision in ('ambiguous', 'identifier_conflict') for match in matches)
    if conflicts or len(identities) != 1:
        return None
    return next(iter(identities))


def _confirmed_url(paper):
    return any(candidate.get('validation_status') == 'verified' and candidate.get('identity_status') == 'verified'
               for candidate in paper.get('pdf_candidates') or [])


def _check_file(paper, manifest, manifest_path):
    if manifest_path is None:
        return False, 'Manifest path not supplied; file existence and checksum were not checked'
    if not paper.get('local_filename') or not paper.get('sha256'):
        return False, 'No local file/checksum recorded'
    root = Path(manifest_path).resolve().parent
    file_root = manifest.get('file_root', '.')
    if file_root not in ('.', 'papers'):
        return False, 'Unexpected manifest file_root'
    path = root / file_root / paper['local_filename']
    storage = (root / file_root).resolve()
    if storage != root and root not in storage.parents:
        return False, 'Unsafe or symlink file root'
    if any(part.is_symlink() for part in [path] + list(path.parents) if part != root and root in part.parents):
        return False, 'Unsafe or symlink file reference'
    if storage not in path.resolve().parents:
        return False, 'Unsafe or symlink file reference'
    try:
        size, digest = file_digest(path)
    except (OSError, ValueError) as exc:
        return False, str(exc)
    if digest != paper['sha256'] or size != paper.get('file_size'):
        return False, 'Saved PDF file size or SHA-256 mismatch'
    return True, 'Local file exists and its size/SHA-256 agree with the parsed manifest'


class PerformanceEvaluator:
    def evaluate(self, collection, truth_data, pdf_report=None, manifest=None, manifest_path=None,
                 reference_only=False):
        GroundTruth.from_dict(truth_data)
        pdf_scope = _stage_scope(pdf_report)
        _stage_scope(manifest)
        metadata = truth_data.get('metadata') or {}
        if 'formal_evaluation_ready' in metadata and not reference_only:
            raise ValueError('Pilot formal metrics require the independent result audit; '
                             'use python -m scripts.ground_truth.evaluate_pilot')
        reviewer = metadata.get('reviewer')
        reviewed_at = metadata.get('reviewed_at')
        human = bool(isinstance(reviewer, str) and reviewer.strip() and
                     isinstance(reviewed_at, str) and reviewed_at.strip() and
                     metadata.get('annotation_state') == 'human_reviewed')
        scoring_data = deepcopy(truth_data)
        if reference_only:
            for paper in scoring_data.get('papers') or []:
                if paper.get('ownership') == 'uncertain' and paper.get('proposed_ownership') in ('belongs', 'not_belongs'):
                    paper['ownership'] = paper['proposed_ownership']
        elif not human:
            raise ValueError('Formal performance metrics require human_reviewed metadata, reviewer and reviewed_at; '
                             'use reference_only for an explicitly provisional public-source reference set')
        elif any(paper['ownership'] != 'uncertain' and paper.get('annotation_status') == 'pending_human_review'
                 for paper in scoring_data['papers']):
            raise ValueError('Adjudicated entries cannot retain pending_human_review annotation status')
        truth = GroundTruth.from_dict(scoring_data)
        positives = {paper['evaluation_id'] for paper in truth.papers if paper['ownership'] == 'belongs'}
        negatives = {paper['evaluation_id'] for paper in truth.papers if paper['ownership'] == 'not_belongs'}
        matcher = GroundTruthMatcher(truth)
        baseline = Evaluator().evaluate(collection, truth)
        statuses = {'verified': set(), 'needs_review': set(), 'rejected': set()}
        for paper in _papers(collection):
            for record in paper.get('records') or [paper]:
                identity = matcher.match(record).evaluation_id
                if identity:
                    statuses[paper.get('ownership_status', 'needs_review')].add(identity)
        retrieved = set().union(*statuses.values())
        conflicts = statuses['verified'] & statuses['rejected'] & (positives | negatives)
        verified = baseline['by_status']['verified']
        rejected = baseline['by_status']['rejected']
        decided_count = (verified['true_positives'] + verified['false_positives'] +
                         rejected['true_positives'] + rejected['false_positives'] - 2 * len(conflicts))
        correct_count = verified['true_positives'] + rejected['false_positives'] - len(conflicts)
        judged_count = baseline['all_candidates']['true_positives'] + baseline['all_candidates']['false_positives']
        pdf_found = set()
        pdf_processed = set()
        pdf_reasons = Counter()
        failure_entries = []
        for paper in _papers(pdf_report):
            identity = _association(paper, matcher)
            if paper.get('validation_status') not in ('not_processed', 'skipped_ownership'):
                if identity in positives:
                    pdf_processed.add(identity)
            if _confirmed_url(paper):
                if identity in positives:
                    pdf_found.add(identity)
            else:
                reasons = pdf_failure_reasons(paper)
                pdf_reasons.update(reasons)
                failure_entries.append({'title': paper.get('title'), 'doi': paper.get('doi'),
                                       'evaluation_id': identity, 'reasons': reasons,
                                       'evidence': paper.get('error_details') or [],
                                       'providers': paper.get('providers') or [],
                                       'candidates': [{key: candidate.get(key) for key in (
                                           'url', 'source', 'http_status', 'validation_status', 'identity_status',
                                           'identity_evidence', 'validation_evidence', 'error_details')}
                                                      for candidate in paper.get('pdf_candidates') or []]})
        attempted = set()
        valid_format = set()
        successful = set()
        unchecked_files = []
        for paper in _papers(manifest):
            identity = _association(paper, matcher)
            status = paper.get('download_status', '')
            if status.startswith('skipped_') and status != 'skipped_existing':
                continue
            if status in ('not_processed', ''):
                continue
            exists, evidence = _check_file(paper, manifest, manifest_path)
            if not exists:
                unchecked_files.append({'evaluation_id': identity, 'title': paper.get('title'), 'reason': evidence})
            format_confirmed = exists and paper.get('format_status') == 'valid'
            content_confirmed = (format_confirmed and paper.get('identity_status') == 'verified' and
                                 paper.get('validation_status') == 'verified')
            if not content_confirmed:
                reasons = pdf_failure_reasons(paper)
                if not exists:
                    reasons.append('local_file_integrity_unconfirmed')
                pdf_reasons.update(reasons)
                failure_entries.append({'title': paper.get('title'), 'evaluation_id': identity, 'stage': 'download',
                                       'reasons': reasons, 'evidence': paper.get('error_details') or [],
                                       'content_validation': paper.get('validation') or {},
                                       'local_file_check': evidence})
            if identity not in positives:
                continue
            attempted.add(identity)
            if format_confirmed:
                valid_format.add(identity)
            if content_confirmed:
                successful.add(identity)
        metrics = {
            'paper_discovery_recall': rate(len(retrieved & positives), len(positives)),
            'verified_paper_recall': rate(len(statuses['verified'] & positives), len(positives)),
            'author_match_precision': rate(verified['true_positives'], verified['true_positives'] + verified['false_positives']),
            'author_decision_accuracy': rate(correct_count, decided_count),
            'author_decision_coverage': rate(decided_count, judged_count),
            'validated_pdf_url_coverage': rate(len(pdf_found), len(positives)),
            'validated_pdf_url_rate_among_processed': rate(len(pdf_found), len(pdf_processed)),
            'actual_download_coverage': rate(len(successful), len(positives)),
            'actual_download_success_rate': rate(len(successful), len(attempted)),
            'download_format_success_rate': rate(len(valid_format), len(attempted)),
        }
        for name, metric in metrics.items():
            available = True
            if name.startswith('validated_pdf_'):
                available = pdf_report is not None
            elif name.startswith('actual_download_') or name == 'download_format_success_rate':
                available = manifest is not None and manifest_path is not None
            metric['available'] = available
            if not available:
                metric['rate'] = None
        fingerprint = hashlib.sha256(json.dumps(scoring_data, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
        formal = human and not reference_only
        recall = metrics['paper_discovery_recall']['rate']
        return {
            'schema_version': 1, 'matching_rules_version': MATCHING_RULES_VERSION,
            'pdf_processing_scope': pdf_scope,
            'metric_scope': 'human_ground_truth' if formal else 'provisional_reference_not_human_ground_truth',
            'formal_metrics': formal, 'ground_truth_fingerprint': fingerprint,
            'reference_metadata': metadata, 'metrics': metrics,
            'recall_target': {'target': 0.8, 'formally_met': bool(formal and recall is not None and recall >= 0.8)},
            'ownership_evaluation': baseline, 'needs_review_diagnostics': ownership_diagnostics(_papers(collection)),
            'pdf_failure_reason_counts': dict(pdf_reasons), 'unavailable_or_unconfirmed_papers': failure_entries,
            'unchecked_local_files': unchecked_files,
            'publication_ids': {'discovered': sorted(retrieved & positives), 'pdf_url_verified': sorted(pdf_found),
                                'download_attempted': sorted(attempted), 'download_content_verified': sorted(successful)},
            'notes': ['Recall covers the supplied reference scope, never all publications without a complete human list.',
                      'Author accuracy excludes abstentions, unjudged entries and status conflicts; coverage is reported separately.',
                      'PDF URL verification is not a successful download.',
                      'Download success requires prior format/content verification AND a current local file size/SHA-256 check.',
                      'Failure categories can overlap; skipped/unprocessed entries are not download failures.'],
        }


def compare_performance(before, after):
    if before['ground_truth_fingerprint'] != after['ground_truth_fingerprint']:
        raise ValueError('Before/after comparison requires the same reference set and labels')
    if before['metric_scope'] != after['metric_scope']:
        raise ValueError('Cannot compare formal human metrics to provisional reference metrics')
    if before.get('matching_rules_version') != after.get('matching_rules_version'):
        raise ValueError('Matching rules differ; evaluate both saved runs again with the same evaluation code')
    changes = {}
    for key, current in after['metrics'].items():
        previous = before['metrics'][key]
        difference = None
        if previous['rate'] is not None and current['rate'] is not None:
            difference = current['rate'] - previous['rate']
        changes[key] = {'before': previous, 'after': current, 'absolute_rate_change': difference}
    return {'schema_version': 1, 'metric_scope': after['metric_scope'],
            'matching_rules_version': after.get('matching_rules_version'),
            'same_pdf_processing_scope': before.get('pdf_processing_scope') == after.get('pdf_processing_scope'),
            'formal_metrics': after['formal_metrics'], 'metrics': changes,
            'recall_target': after['recall_target'],
            'notes': ['Use identical publication and processing scopes; network observations may vary between runs.']}

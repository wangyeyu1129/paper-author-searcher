"""Compare saved discovery runs on an identical cohort without ground-truth URLs."""

import argparse
import hashlib
import json
import logging
import typing
from collections import Counter
from pathlib import Path
from typing import Any, Dict

from .jsonio import read_json, write_json
from .logging_config import add_logging_arguments, configure_from_arguments
from .pdf_downloader import paper_key
from .pdf_search_status import SEARCH_COMPLETION_DEFINITION, provider_search_complete, search_satisfaction
from .performance_diagnostics import pdf_failure_reasons
from .performance_evaluation import _check_file, rate
from .status import DownloadStatus, FormatStatus, IdentityStatus, PDFValidationStatus

logger = logging.getLogger(__name__)


def _cohort(report):
    papers = report.get('papers')
    if not isinstance(papers, list) or any(not isinstance(paper, dict) for paper in papers):
        raise ValueError('Each PDF report must contain a paper list')
    selected = {}
    for paper in papers:
        if paper.get('validation_status') in (PDFValidationStatus.NOT_PROCESSED, PDFValidationStatus.SKIPPED_OWNERSHIP):
            continue
        if paper.get('validation_status') not in (
            PDFValidationStatus.VERIFIED,
            PDFValidationStatus.UNVERIFIED,
            PDFValidationStatus.NOT_FOUND,
        ):
            raise ValueError('Expected PDF URL validation status in the saved discovery report')
        key = paper_key(paper)
        if key in selected:
            raise ValueError('Duplicate paper identity in processed cohort')
        selected[key] = paper
    if not selected:
        raise ValueError('PDF report has no processed cohort')
    return selected


def _metadata_fingerprint(selected):
    fields = (
        'title',
        'authors',
        'year',
        'doi',
        'source',
        'source_paper_id',
        'source_author_id',
        'ownership_status',
        'ownership_evidence',
    )
    values = {key: {field: paper.get(field) for field in fields} for key, paper in selected.items()}
    encoded = json.dumps(values, sort_keys=True, ensure_ascii=False).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def summarize_runs(run_directories):
    cohort = None
    metadata_fingerprint = None
    verified_papers, urls, files, content = set(), set(), set(), set()
    attempted, failed, review = set(), set(), set()
    failures: typing.Counter[str] = Counter()
    source_contributions: Dict[str, Any] = {}
    file_checks = []
    query_statistics = []
    search_observations: Dict[str, Any] = {}
    document_types: typing.Counter[str] = Counter()
    full_text_content = set()
    classified_documents = False
    satisfied_papers = set()
    satisfaction_observations: Dict[str, Any] = {}
    for directory in run_directories:
        root = Path(directory)
        report = read_json(root / 'pdf_urls.json')
        selected = _cohort(report)
        if cohort is not None and set(selected) != cohort:
            raise ValueError('Processed paper cohorts differ; coverage rates cannot be compared')
        fingerprint = _metadata_fingerprint(selected)
        if metadata_fingerprint is not None and fingerprint != metadata_fingerprint:
            raise ValueError('Cohort metadata or ownership decisions differ')
        metadata_fingerprint = fingerprint
        cohort = set(selected)
        query_statistics.append({'run_directory': str(root), 'statistics': report.get('statistics') or {}})
        for key, paper in selected.items():
            source_states = search_observations.setdefault(key, {})
            for provider in paper.get('providers') or []:
                complete = provider_search_complete(provider)
                source_states.setdefault(provider.get('source'), []).append(complete)
            failures.update(pdf_failure_reasons(paper))
            for candidate in paper.get('pdf_candidates') or []:
                if (
                    candidate.get('validation_status') != PDFValidationStatus.VERIFIED
                    or candidate.get('identity_status') != IdentityStatus.VERIFIED
                ):
                    continue
                verified_papers.add(key)
                urls.add(candidate.get('final_url') or candidate['url'])
                sources = {candidate.get('source')}
                sources.update(item.get('provider') for item in candidate.get('provenance') or [])
                for source in sources - {None}:
                    source_contributions.setdefault(source, set()).add(key)
        manifest_path = root / 'manifest.json'
        if not manifest_path.exists():
            continue
        manifest = read_json(manifest_path)
        for paper in manifest.get('papers') or []:
            key = paper_key(paper)
            if key not in cohort:
                continue
            status = paper.get('download_status') or ''
            if status in ('', DownloadStatus.NOT_PROCESSED) or (
                status.startswith('skipped_') and status != DownloadStatus.SKIPPED_EXISTING
            ):
                continue
            attempted.add(key)
            if status == DownloadStatus.FAILED:
                failed.add(key)
            exists, evidence = _check_file(paper, manifest, manifest_path)
            file_checks.append(
                {'paper_key': key, 'run_directory': str(root), 'file_integrity_confirmed': exists, 'evidence': evidence}
            )
            satisfied, reason = search_satisfaction(paper, exists)
            if satisfied is True:
                satisfied_papers.add(key)
            previous = satisfaction_observations.get(key)
            if previous is None or satisfied is True or previous['search_sufficiently_satisfied'] is False:
                satisfaction_observations[key] = {
                    'paper_key': key,
                    'search_sufficiently_satisfied': satisfied,
                    'reason': reason,
                    'file_integrity_confirmed': exists,
                }
            if not exists or paper.get('format_status') != FormatStatus.VALID:
                continue
            files.add(key)
            document_type = paper.get('document_type') or (paper.get('validation') or {}).get('document_type')
            classified_documents = classified_documents or document_type is not None
            document_types[document_type or 'unknown'] += 1
            if (
                paper.get('identity_status') == IdentityStatus.VERIFIED
                and paper.get('validation_status') == PDFValidationStatus.VERIFIED
            ):
                content.add(key)
                if document_type in ('full_text', 'preprint'):
                    full_text_content.add(key)
            elif paper.get('validation_status') == PDFValidationStatus.NEEDS_REVIEW:
                review.add(key)
    if cohort is None:
        raise ValueError('At least one saved run is required')
    digest = hashlib.sha256(json.dumps(sorted(cohort)).encode('utf-8')).hexdigest()
    complete_searches = sum(
        bool(states) and all(any(observations) for observations in states.values())
        for states in search_observations.values()
    )
    return {
        'cohort_size': len(cohort),
        'cohort_fingerprint': digest,
        'metadata_fingerprint': metadata_fingerprint,
        'verified_pdf_url_coverage': rate(len(verified_papers), len(cohort)),
        'distinct_verified_urls': len(urls),
        'download_attempted_papers': len(attempted),
        'valid_downloaded_file_coverage': rate(len(files), len(cohort)),
        'content_confirmed_coverage': rate(len(content), len(cohort)),
        'download_success_rate': rate(len(files), len(attempted)),
        'needs_review_ratio_among_valid_files': rate(len(review - content), len(files)),
        'search_completion_rate': rate(complete_searches, len(cohort)),
        'search_completion_definition': SEARCH_COMPLETION_DEFINITION,
        'search_sufficiently_satisfied_rate': rate(len(satisfied_papers), len(cohort))
        if classified_documents
        else None,
        'search_satisfaction_unknown_papers': sum(
            item['search_sufficiently_satisfied'] is None for item in satisfaction_observations.values()
        ),
        'paper_search_satisfaction': [
            satisfaction_observations.get(
                key,
                {
                    'paper_key': key,
                    'search_sufficiently_satisfied': False,
                    'reason': 'no_integrity_checked_local_file',
                    'file_integrity_confirmed': False,
                },
            )
            for key in sorted(cohort)
        ],
        'document_type_observations': dict(document_types),
        'full_text_confirmed_coverage': rate(len(full_text_content), len(cohort)) if classified_documents else None,
        'content_needs_review_papers': len(review - content),
        'failed_without_valid_file_papers': len(failed - files),
        'source_verified_paper_contributions': {source: len(keys) for source, keys in source_contributions.items()},
        'failure_observation_counts': dict(failures),
        'file_checks': file_checks,
        'runs': query_statistics,
        'verified_paper_keys': sorted(verified_papers),
        'content_confirmed_paper_keys': sorted(content),
        'metric_note': 'Saved URL probes, parser decisions and current local size/SHA-256 checks. '
        'Content confirmation is automated, not human ground truth. '
        'Failure observations can overlap across runs.',
    }


def compare_runs(before_directories, after_directories):
    before = summarize_runs(before_directories)
    after = summarize_runs(after_directories)
    if before['cohort_fingerprint'] != after['cohort_fingerprint']:
        raise ValueError('Before/after processed paper cohorts differ')
    if before['metadata_fingerprint'] != after['metadata_fingerprint']:
        raise ValueError('Before/after metadata or ownership decisions differ')
    gained = sorted(set(after['verified_paper_keys']) - set(before['verified_paper_keys']))
    lost = sorted(set(before['verified_paper_keys']) - set(after['verified_paper_keys']))
    return {
        'before': before,
        'after': after,
        'newly_verified_pdf_paper_keys': gained,
        'lost_verified_pdf_paper_keys': lost,
        'url_coverage_change': after['verified_pdf_url_coverage']['rate'] - before['verified_pdf_url_coverage']['rate'],
        'content_coverage_change': after['content_confirmed_coverage']['rate']
        - before['content_confirmed_coverage']['rate'],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description='Offline PDF discovery comparison on the same processed paper cohort')
    parser.add_argument(
        '--before',
        action='append',
        type=Path,
        required=True,
        help='Saved workflow run directory; repeat to combine runs',
    )
    parser.add_argument('--after', action='append', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    add_logging_arguments(parser)
    args = parser.parse_args(argv)
    configure_from_arguments(args)
    inputs = [directory / name for directory in args.before + args.after for name in ('pdf_urls.json', 'manifest.json')]
    if args.output.resolve() in {path.resolve() for path in inputs}:
        parser.error('Output must not overwrite source reports')
    try:
        result = compare_runs(args.before, args.after)
        write_json(args.output, result)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    for label in ('before', 'after'):
        value = result[label]
        print(
            '{}: URL {}/{}; content confirmed {}/{}'.format(
                label,
                value['verified_pdf_url_coverage']['numerator'],
                value['cohort_size'],
                value['content_confirmed_coverage']['numerator'],
                value['cohort_size'],
            )
        )
    print(f'Saved comparison: {args.output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

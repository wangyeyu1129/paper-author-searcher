"""Compare saved discovery runs on an identical cohort without ground-truth URLs."""
import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from .pdf_downloader import paper_key
from .performance_diagnostics import pdf_failure_reasons
from .performance_evaluation import _check_file, rate
from .workflow import read_json, write_json


def _cohort(report):
    papers = report.get('papers')
    if not isinstance(papers, list) or any(not isinstance(paper, dict) for paper in papers):
        raise ValueError('Each PDF report must contain a paper list')
    selected = {}
    for paper in papers:
        if paper.get('validation_status') in ('not_processed', 'skipped_ownership'):
            continue
        if paper.get('validation_status') not in ('verified', 'unverified', 'not_found'):
            raise ValueError('Expected PDF URL validation status in the saved discovery report')
        key = paper_key(paper)
        if key in selected:
            raise ValueError('Duplicate paper identity in processed cohort')
        selected[key] = paper
    if not selected:
        raise ValueError('PDF report has no processed cohort')
    return selected


def _metadata_fingerprint(selected):
    fields = ('title', 'authors', 'year', 'doi', 'source', 'source_paper_id', 'source_author_id',
              'ownership_status', 'ownership_evidence')
    values = {key: {field: paper.get(field) for field in fields} for key, paper in selected.items()}
    encoded = json.dumps(values, sort_keys=True, ensure_ascii=False).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def summarize_runs(run_directories):
    cohort = None
    metadata_fingerprint = None
    verified_papers, urls, files, content = set(), set(), set(), set()
    attempted, failed, review = set(), set(), set()
    failures, source_contributions = Counter(), {}
    file_checks = []
    query_statistics = []
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
        query_statistics.append({'run_directory': str(root),
                                 'statistics': report.get('statistics') or {}})
        for key, paper in selected.items():
            failures.update(pdf_failure_reasons(paper))
            for candidate in paper.get('pdf_candidates') or []:
                if candidate.get('validation_status') != 'verified' or candidate.get('identity_status') != 'verified':
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
            if status in ('', 'not_processed') or (status.startswith('skipped_') and status != 'skipped_existing'):
                continue
            attempted.add(key)
            if status == 'failed':
                failed.add(key)
            exists, evidence = _check_file(paper, manifest, manifest_path)
            file_checks.append({'paper_key': key, 'run_directory': str(root),
                                'file_integrity_confirmed': exists, 'evidence': evidence})
            if not exists or paper.get('format_status') != 'valid':
                continue
            files.add(key)
            if paper.get('identity_status') == 'verified' and paper.get('validation_status') == 'verified':
                content.add(key)
            elif paper.get('identity_status') == 'needs_review':
                review.add(key)
    if cohort is None:
        raise ValueError('At least one saved run is required')
    digest = hashlib.sha256(json.dumps(sorted(cohort)).encode('utf-8')).hexdigest()
    return {'cohort_size': len(cohort), 'cohort_fingerprint': digest, 'metadata_fingerprint': metadata_fingerprint,
            'verified_pdf_url_coverage': rate(len(verified_papers), len(cohort)),
            'distinct_verified_urls': len(urls), 'download_attempted_papers': len(attempted),
            'valid_downloaded_file_coverage': rate(len(files), len(cohort)),
            'content_confirmed_coverage': rate(len(content), len(cohort)),
            'download_success_rate': rate(len(files), len(attempted)),
            'content_needs_review_papers': len(review - content),
            'failed_without_valid_file_papers': len(failed - files),
            'source_verified_paper_contributions': {source: len(keys) for source, keys in source_contributions.items()},
            'failure_observation_counts': dict(failures), 'file_checks': file_checks,
            'runs': query_statistics, 'verified_paper_keys': sorted(verified_papers),
            'content_confirmed_paper_keys': sorted(content),
            'metric_note': 'Saved URL probes, parser decisions and current local size/SHA-256 checks. '
                           'Content confirmation is automated, not human ground truth. '
                           'Failure observations can overlap across runs.'}


def compare_runs(before_directories, after_directories):
    before = summarize_runs(before_directories)
    after = summarize_runs(after_directories)
    if before['cohort_fingerprint'] != after['cohort_fingerprint']:
        raise ValueError('Before/after processed paper cohorts differ')
    if before['metadata_fingerprint'] != after['metadata_fingerprint']:
        raise ValueError('Before/after metadata or ownership decisions differ')
    gained = sorted(set(after['verified_paper_keys']) - set(before['verified_paper_keys']))
    lost = sorted(set(before['verified_paper_keys']) - set(after['verified_paper_keys']))
    return {'before': before, 'after': after, 'newly_verified_pdf_paper_keys': gained,
            'lost_verified_pdf_paper_keys': lost,
            'url_coverage_change': after['verified_pdf_url_coverage']['rate'] - before['verified_pdf_url_coverage']['rate'],
            'content_coverage_change': after['content_confirmed_coverage']['rate'] - before['content_confirmed_coverage']['rate']}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Offline PDF discovery comparison on the same processed paper cohort')
    parser.add_argument('--before', action='append', type=Path, required=True, help='Saved workflow run directory; repeat to combine runs')
    parser.add_argument('--after', action='append', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
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
        print('{}: URL {}/{}; content confirmed {}/{}'.format(
            label, value['verified_pdf_url_coverage']['numerator'], value['cohort_size'],
            value['content_confirmed_coverage']['numerator'], value['cohort_size']))
    print('Saved comparison: {}'.format(args.output))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

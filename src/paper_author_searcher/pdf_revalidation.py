"""Offline, checksum-checked content revalidation without altering saved files."""

import argparse
import logging
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from .jsonio import read_json, write_json
from .logging_config import add_logging_arguments, configure_from_arguments
from .pdf_downloader import paper_key
from .pdf_file_validation import PDFFileValidator, validation_target
from .performance_evaluation import _check_file
from .status import FormatStatus, PDFValidationStatus

logger = logging.getLogger(__name__)


def _selected_candidate(paper, saved):
    saved_urls = {saved.get('pdf_url'), saved.get('final_url')} - {None, ''}
    for candidate in paper.get('pdf_candidates') or []:
        if saved_urls.intersection({candidate.get('url'), candidate.get('final_url')}):
            return candidate
    return None


def revalidate(manifest_path, pdf_report_path, only_needs_review=False, validator=None):
    manifest_path = Path(manifest_path)
    manifest = read_json(manifest_path)
    pdf_report = read_json(pdf_report_path)
    if not isinstance(manifest.get('papers'), list) or not isinstance(pdf_report.get('papers'), list):
        raise ValueError('Expected saved manifest and PDF URL report paper lists')
    papers = {paper_key(paper): paper for paper in pdf_report['papers']}
    validator = validator or PDFFileValidator()
    entries = []
    for saved in manifest['papers']:
        if not saved.get('local_filename') or saved.get('format_status') != FormatStatus.VALID:
            continue
        if only_needs_review and saved.get('validation_status') != PDFValidationStatus.NEEDS_REVIEW:
            continue
        key = paper_key(saved)
        entry = {
            'paper_key': key,
            'title': saved.get('title'),
            'local_filename': saved['local_filename'],
            'ownership_status': saved.get('ownership_status'),
            'sha256': saved.get('sha256'),
            'before': saved.get('validation') or {},
            'before_status': saved.get('validation_status'),
        }
        entries.append(entry)
        exists, evidence = _check_file(saved, manifest, manifest_path)
        entry['file_integrity_confirmed'] = exists
        entry['file_check'] = evidence
        paper = papers.get(key)
        if not exists or paper is None:
            entry['error'] = evidence if not exists else 'No matching saved PDF report entry'
            continue
        candidate = _selected_candidate(paper, saved)
        path = manifest_path.resolve().parent / manifest.get('file_root', '.') / saved['local_filename']
        entry['after'] = asdict(validator.validate(path, validation_target(paper, candidate)))
    counts = Counter(entry.get('after', {}).get('validation_status', 'not_evaluated') for entry in entries)
    types = Counter(entry.get('after', {}).get('document_type', 'unknown') for entry in entries)
    before_counts = Counter(entry['before_status'] for entry in entries)
    newly_verified = []
    regressions = []
    full_text_confirmed = 0
    for entry in entries:
        after = entry.get('after') or {}
        if after.get('validation_status') == PDFValidationStatus.VERIFIED:
            if entry['before_status'] != PDFValidationStatus.VERIFIED:
                newly_verified.append(entry['paper_key'])
            if after.get('document_type') in ('full_text', 'preprint'):
                full_text_confirmed += 1
        elif entry['before_status'] == PDFValidationStatus.VERIFIED:
            regressions.append(entry['paper_key'])
    return {
        'report_type': 'offline_pdf_content_revalidation',
        'manifest_path': str(manifest_path.resolve()),
        'pdf_report_path': str(Path(pdf_report_path).resolve()),
        'only_needs_review': only_needs_review,
        'statistics': {
            'selected_files': len(entries),
            'validation_status_counts': dict(counts),
            'before_validation_status_counts': dict(before_counts),
            'document_type_counts': dict(types),
            'full_text_confirmed_files': full_text_confirmed,
            'newly_verified_paper_keys': newly_verified,
            'regressed_paper_keys': regressions,
        },
        'papers': entries,
        'scope_note': 'Current local content parsing with original metadata and verified URL bindings; '
        'no new API queries, downloads, ownership changes or manual ground-truth labels.',
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Revalidate existing PDF contents without network calls or file changes'
    )
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--pdf-report', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--only-needs-review', action='store_true')
    add_logging_arguments(parser)
    args = parser.parse_args(argv)
    configure_from_arguments(args)
    if args.output.resolve() in {args.manifest.resolve(), args.pdf_report.resolve()}:
        parser.error('Output must differ from saved inputs')
    try:
        result = revalidate(args.manifest, args.pdf_report, args.only_needs_review)
        write_json(args.output, result)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    statistics = result['statistics']
    counts = statistics['validation_status_counts']
    print(
        'Checked files: {}; verified={}; needs_review={}; not_evaluated={}; confirmed full text={}'.format(
            statistics['selected_files'],
            counts.get(PDFValidationStatus.VERIFIED, 0),
            counts.get(PDFValidationStatus.NEEDS_REVIEW, 0),
            counts.get('not_evaluated', 0),
            statistics['full_text_confirmed_files'],
        )
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

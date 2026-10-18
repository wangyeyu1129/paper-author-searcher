"""Standalone URL resolution for saved collector JSON, with no PDF file output."""

import argparse
import json
from dataclasses import replace
from pathlib import Path

from .config import DEFAULT_PDF
from .http import HttpClient
from .pdf_resolver import PDFResolver
from .pdf_settings import contact_email


def main(argv=None):
    parser = argparse.ArgumentParser(description='Find and lightly validate public PDF URLs from saved papers')
    parser.add_argument('--input', type=Path, required=True, help='Saved Paper Collector JSON')
    parser.add_argument('--output', type=Path, required=True, help='PDF URL report JSON (no PDF files)')
    parser.add_argument(
        '--include-needs-review', action='store_true', help='Also process unconfirmed ownership entries'
    )
    parser.add_argument(
        '--max-papers', type=int, help='Process the first N eligible entries; mark the remainder unprocessed'
    )
    parser.add_argument('--unpaywall-email', help='Real contact email; alternatively set UNPAYWALL_EMAIL')
    parser.add_argument(
        '--require-unpaywall', action='store_true', help='Fail before requests if a valid contact is not configured'
    )
    parser.add_argument(
        '--bibliography-url',
        action='append',
        default=[],
        help='Public author, repository or conference bibliography to inspect',
    )
    parser.add_argument('--timeout', type=float, help='Override the configured HTTP timeout in seconds')
    parser.add_argument('--retries', type=int, help='Override the configured HTTP retry count')
    args = parser.parse_args(argv)
    if args.input.resolve() == args.output.resolve():
        parser.error('Output must differ from the input archive')
    if args.timeout is not None and args.timeout <= 0:
        parser.error('--timeout must be positive')
    if args.retries is not None and args.retries < 0:
        parser.error('--retries must be nonnegative')
    if args.require_unpaywall:
        try:
            if not contact_email(args.unpaywall_email, DEFAULT_PDF.unpaywall_email_environment):
                raise ValueError('Set UNPAYWALL_EMAIL to your real contact email before running')
        except ValueError as exc:
            parser.error(str(exc))
    try:
        with args.input.open(encoding='utf-8') as handle:
            saved = json.load(handle)
        if not isinstance(saved, dict):
            raise ValueError('Input must be a JSON object')
        resolver = PDFResolver(
            client=HttpClient(timeout=args.timeout, retries=args.retries),
            unpaywall_email=args.unpaywall_email,
            config=replace(DEFAULT_PDF, bibliography_urls=tuple(args.bibliography_url)),
        )
        result = resolver.resolve(saved, args.include_needs_review, args.max_papers).to_dict()
        with args.output.open('w', encoding='utf-8') as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    statistics = result['statistics']
    print(
        'Total: {total_papers}; processed: {processed_papers}; valid PDF papers: {valid_pdf_papers}; '
        'unverified only: {unverified_only_papers}; not found: {not_found_papers}'.format(**statistics)
    )
    print(
        'Distinct verified PDF URLs: {distinct_verified_pdf_urls}; skipped ownership: '
        '{skipped_ownership_papers}; unprocessed: {not_processed_papers}; incomplete searches: '
        '{incomplete_search_papers}'.format(**statistics)
    )
    for source, contribution in statistics['source_contributions'].items():
        print(
            '{}: {} candidates, {} verified, {} papers, {} errors'.format(
                source,
                contribution['candidate_urls'],
                contribution['verified_urls'],
                contribution['papers_with_valid_pdf'],
                contribution['errors'],
            )
        )
    print(f'Saved URL report: {args.output}')
    if statistics['incomplete_search_papers']:
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

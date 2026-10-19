"""One command for live searches or continuation from a saved checkpoint."""

import argparse
import json
import logging
import math
from collections.abc import Sequence
from pathlib import Path

from .composition import RunOptions, ServiceContainer
from .config import AMBIGUITY_POLICIES, AmbiguityPolicy
from .logging_config import add_logging_arguments, configure_from_arguments
from .models import ProfessorQuery
from .settings import Settings, add_settings_argument, load_settings, validate_contact_email
from .status import RunStatus
from .workflow import MODES, load_checkpoint

logger = logging.getLogger(__name__)


def add_hint_arguments(parser):
    """Optional identity hints; each one makes namesake disambiguation far more reliable."""
    parser.add_argument('--orcid', help='ORCID iD of the researcher')
    parser.add_argument('--homepage', help='Public homepage or CV URL; DOIs/arXiv links on it become anchors')
    parser.add_argument('--email', help='Institutional email (recorded with the query)')
    parser.add_argument(
        '--known-paper',
        action='append',
        default=[],
        help='DOI, arXiv ID or exact title of a paper by this researcher (repeatable)',
    )


def query_from_args(args):
    return ProfessorQuery(
        args.name,
        args.university,
        args.department,
        args.research,
        args.university_alias,
        orcid=getattr(args, 'orcid', None),
        homepage=getattr(args, 'homepage', None),
        email=getattr(args, 'email', None),
        known_papers=list(getattr(args, 'known_paper', [])),
    )


def add_workflow_arguments(parser):
    add_settings_argument(parser)
    parser.add_argument('--mode', choices=MODES, help='metadata, pdf-urls, or full workflow')
    parser.add_argument('--input', type=Path, help='Resolver, Collector, PDF URL JSON or workflow report')
    parser.add_argument('--output-dir', type=Path, default=Path('output'))
    parser.add_argument('--resume-dir', type=Path, help='Reuse a workflow directory and its completed downloads')
    parser.add_argument('--max-downloads', type=int, help='Limit attempted papers; completed files do not count')
    parser.add_argument('--max-pdf-papers', type=int, help='Limit PDF lookups; keep remaining papers unprocessed')
    parser.add_argument('--max-pages', type=int, help='Limit collection pages per Author ID; marks coverage incomplete')
    parser.add_argument('--page-size', type=int, help='Collection page size (1-200) for both paginated APIs')
    parser.add_argument('--timeout', type=float)
    parser.add_argument('--retries', type=int)
    parser.add_argument('--max-file-mb', type=float)
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
    parser.add_argument(
        '--include-needs-review', action='store_true', help='Explicit ownership opt-in; verified PDF URL still required'
    )
    parser.add_argument(
        '--max-cost-usd',
        type=float,
        default=0.5,
        help='Hard cap on estimated metered API spend (OpenAlex) for this run; 0 = only free requests',
    )
    parser.add_argument(
        '--llm-adjudication',
        action='store_true',
        help='Ask Claude to judge close identity contests (needs the "llm" extra and credentials)',
    )
    parser.add_argument(
        '--ambiguity-policy',
        choices=AMBIGUITY_POLICIES,
        default=AmbiguityPolicy.COLLECT_CONTENDERS,
        help='On an unconfirmed identity: stop, or collect competing profiles for review (default)',
    )


def _validate(parser, args):
    if args.require_unpaywall:
        try:
            email = args.unpaywall_email or load_settings(args).credentials.unpaywall_email
            if not validate_contact_email(email):
                raise ValueError('Set UNPAYWALL_EMAIL to your real contact email before running')
        except ValueError as exc:
            parser.error(str(exc))
    if args.input:
        if args.name or args.university or args.department or args.research:
            parser.error('--input uses the saved identity; do not combine with professor inputs')
    elif not args.name or not args.university:
        parser.error('--name and --university are required unless --input is given')
    if args.resume_dir and (not args.input or args.input.resolve().parent != args.resume_dir.resolve()):
        parser.error('--resume-dir requires an input checkpoint from the same workflow directory')
    for name in ('max_downloads', 'max_pdf_papers', 'max_pages'):
        if getattr(args, name) is not None and getattr(args, name) < 1:
            parser.error('--{} must be positive'.format(name.replace('_', '-')))
    if args.page_size is not None and not 1 <= args.page_size <= 200:
        parser.error('--page-size must be between 1 and 200')
    for name in ('timeout', 'max_file_mb'):
        value = getattr(args, name)
        if value is not None and (not math.isfinite(value) or value <= 0):
            parser.error('--{} must be finite and positive'.format(name.replace('_', '-')))
    if args.retries is not None and args.retries < 0:
        parser.error('--retries must be nonnegative')


def build_container(args, settings: Settings | None = None) -> ServiceContainer:
    return ServiceContainer(settings or load_settings(args), RunOptions.from_namespace(args))


def build_workflow(args, settings: Settings | None = None):
    return build_container(args, settings).workflow()


def _show_candidates(report, verbose=False):
    identity = report.get('identity') or {}
    candidates = identity.get('candidates') or []
    visible = candidates if verbose else candidates[:2]
    for index, candidate in enumerate(visible, 1):
        print(
            '{}: {} | affiliations={} | score={} | confidence={} | status={}'.format(
                index,
                candidate['name'],
                ', '.join(candidate.get('matched_affiliations') or candidate.get('affiliations') or []),
                candidate.get('match_score'),
                candidate.get('identity_confidence'),
                candidate.get('status'),
            )
        )
        evidence = candidate.get('evidence') or []
        print(
            '  ORCID: {}; evidence: {}'.format(candidate.get('orcid'), '; '.join(evidence if verbose else evidence[:3]))
        )
        ids = candidate.get('author_id_aliases') or {}
        for source, author_id in candidate.get('author_ids', {}).items():
            for value in sorted({*ids.get(source, []), author_id}):
                print(f'  --select-author {source}:{value}')
    if len(visible) < len(candidates):
        print(
            f'{len(candidates) - len(visible)} additional candidates saved in professor.json; use --verbose for details'
        )
    print(
        'Continue with --input {} --select-author SOURCE:AUTHOR_ID'.format(
            Path(report['output_directory']) / 'professor.json'
        )
    )


def run_arguments(parser, args):
    _validate(parser, args)
    try:
        settings = load_settings(args)
        saved = load_checkpoint(args.input, settings.storage.intern_strings) if args.input else None
        query = None if saved is not None else query_from_args(args)
        workflow = build_workflow(args, settings)
        report = workflow.run(
            query=query,
            saved=saved,
            output_dir=args.output_dir,
            mode=args.mode or 'full',
            selected_author=args.select_author,
            include_needs_review=args.include_needs_review,
            maximum_pdf_papers=args.max_pdf_papers,
            maximum_downloads=args.max_downloads,
            resume_dir=args.resume_dir,
        )
    except KeyboardInterrupt:
        logger.warning('Interrupted. Use the saved report and --resume-dir to continue.')
        return 130
    except (OSError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    if report['run_status'] == RunStatus.IDENTITY_CONFIRMATION_REQUIRED:
        _show_candidates(report, args.verbose)
    collection = report.get('collection_statistics') or {}
    print(
        'Papers: raw={}, deduplicated={}, ownership={}'.format(
            collection.get('raw_records_received', 'unknown'),
            collection.get('deduplicated_entries', 'unknown'),
            collection.get('ownership_counts', {}),
        )
    )
    if 'pdf_statistics' in report:
        statistics = report['pdf_statistics']
        print(
            'PDF URLs: validated={}, unverified={}, unprocessed={}'.format(
                statistics.get('valid_pdf_papers', 0),
                statistics.get('unverified_only_papers', 0),
                statistics.get('not_processed_papers', 0),
            )
        )
    if 'download_statistics' in report:
        statistics = report['download_statistics']
        print(
            'Downloads: new={}, reused={}, content verified={}, needs review={}, failed={}'.format(
                statistics['downloaded_files'],
                statistics['reused_existing_papers'],
                statistics['verified_papers'],
                statistics['needs_review_papers'],
                statistics['failed_papers'],
            )
        )
    missing = report.get('metadata_audit', {}).get('missing_input_fields', {})
    if missing.get('year') or missing.get('authors'):
        logger.warning(
            'Missing input metadata: year=%s, authors=%s (no values invented)',
            missing.get('year', 0),
            missing.get('authors', 0),
        )
    if args.verbose:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    print(
        'Status: {}; stage errors: {}; report: {}'.format(
            report['run_status'], len(report['errors']), Path(report['output_directory']) / 'report.json'
        )
    )
    return 0 if report['run_status'] == RunStatus.COMPLETED else 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Resolve a professor, collect papers and obtain public PDFs')
    parser.add_argument('--name')
    parser.add_argument('--university')
    parser.add_argument('--department')
    parser.add_argument('--research', action='append', default=[])
    parser.add_argument('--university-alias', action='append', default=[])
    parser.add_argument('--select-author', metavar='SOURCE:AUTHOR_ID')
    parser.add_argument('--verbose', action='store_true')
    add_hint_arguments(parser)
    add_workflow_arguments(parser)
    add_logging_arguments(parser)
    args = parser.parse_args(argv)
    configure_from_arguments(args)
    return run_arguments(parser, args)


if __name__ == '__main__':
    raise SystemExit(main())

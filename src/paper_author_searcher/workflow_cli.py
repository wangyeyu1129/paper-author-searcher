"""One command for live searches or continuation from a saved checkpoint."""
import argparse
import json
import math
from dataclasses import replace
from pathlib import Path

from .config import AMBIGUITY_POLICIES, DEFAULT_COLLECTION, DEFAULT_DOWNLOAD, DEFAULT_MATCHING, DEFAULT_PDF
from .http import HttpClient
from .http_budget import budgeted_client
from .http_cache import cached_client
from .identity_anchors import ScholarlyAnchorFinder
from .models import ProfessorQuery
from .paper_collector import PaperCollector
from .paper_providers import OpenAlexPaperProvider, SemanticScholarPaperProvider, DBLPPaperProvider
from .pdf_resolver import PDFResolver
from .pdf_settings import contact_email
from .providers import OpenAlexProvider, SemanticScholarProvider, DBLPProvider
from .resolver import ProfessorResolver
from .workflow import MODES, PaperSearchWorkflow, load_checkpoint


def add_hint_arguments(parser):
    """Optional identity hints; each one makes namesake disambiguation far more reliable."""
    parser.add_argument('--orcid', help='ORCID iD of the researcher')
    parser.add_argument('--homepage', help='Public homepage or CV URL; DOIs/arXiv links on it become anchors')
    parser.add_argument('--email', help='Institutional email (recorded with the query)')
    parser.add_argument('--known-paper', action='append', default=[],
                        help='DOI, arXiv ID or exact title of a paper by this researcher (repeatable)')


def query_from_args(args):
    return ProfessorQuery(args.name, args.university, args.department, args.research, args.university_alias,
                          orcid=getattr(args, 'orcid', None), homepage=getattr(args, 'homepage', None),
                          email=getattr(args, 'email', None), known_papers=list(getattr(args, 'known_paper', [])))


def add_workflow_arguments(parser):
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
    parser.add_argument('--require-unpaywall', action='store_true', help='Fail before requests if a valid contact is not configured')
    parser.add_argument('--bibliography-url', action='append', default=[], help='Public author, repository or conference bibliography to inspect')
    parser.add_argument('--include-needs-review', action='store_true', help='Explicit ownership opt-in; verified PDF URL still required')
    parser.add_argument('--max-cost-usd', type=float, default=0.5,
                        help='Hard cap on estimated metered API spend (OpenAlex) for this run; 0 = only free requests')
    parser.add_argument('--llm-adjudication', action='store_true',
                        help='Ask Claude to judge close identity contests (needs the "llm" extra and credentials)')
    parser.add_argument('--ambiguity-policy', choices=AMBIGUITY_POLICIES, default='collect_contenders',
                        help='On an unconfirmed identity: stop, or collect competing profiles for review (default)')


class ProgressPrinter:
    def __init__(self, verbose=False):
        self.verbose = verbose
        self.stage = None
        self.bucket = -1

    def __call__(self, stage, completed=None, total=None):
        new_stage = stage != self.stage
        bucket = int(10 * completed / total) if completed is not None and total else -1
        if new_stage or self.verbose or bucket > self.bucket:
            suffix = ' [{}/{}]'.format(completed, total) if completed is not None else ''
            print(stage + suffix, flush=True)
        self.stage = stage
        self.bucket = bucket


def _validate(parser, args):
    if args.require_unpaywall:
        try:
            if not contact_email(args.unpaywall_email, DEFAULT_PDF.unpaywall_email_environment):
                raise ValueError('Set UNPAYWALL_EMAIL to your real contact email before running')
        except ValueError as exc:
            parser.error(str(exc))
    if args.input:
        if args.name or args.university or args.department or args.research:
            parser.error('--input uses the saved identity; do not combine with professor inputs')
    elif not args.name or not args.university:
        parser.error('--name and --university are required unless --input is given')
    if args.resume_dir:
        if not args.input or args.input.resolve().parent != args.resume_dir.resolve():
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


def build_client(args):
    # cache -> budget -> network: cached replays are free, only real requests are priced.
    metered = budgeted_client(HttpClient(timeout=args.timeout, retries=args.retries),
                              getattr(args, 'max_cost_usd', None), getattr(args, 'cost_ledger', 'default'))
    return cached_client(metered, getattr(args, 'http_cache_dir', None), getattr(args, 'http_cache_mode', None))


def build_identity_providers(client, matching_config=DEFAULT_MATCHING):
    return [OpenAlexProvider(client=client, matching_config=matching_config),
            SemanticScholarProvider(client=client), DBLPProvider(client=client)]


def build_resolver(client, providers, matching_config=DEFAULT_MATCHING, llm_adjudication=False):
    adjudicator = None
    if llm_adjudication:
        from .identity_adjudication import ClaudeIdentityAdjudicator
        adjudicator = ClaudeIdentityAdjudicator()
    return ProfessorResolver(providers, config=matching_config,
                             anchor_finder=ScholarlyAnchorFinder(client, matching_config=matching_config),
                             adjudicator=adjudicator)


def build_workflow(args):
    client = build_client(args)
    providers = build_identity_providers(client)
    collection_config = DEFAULT_COLLECTION
    if args.max_pages is not None:
        collection_config = replace(collection_config, maximum_pages=args.max_pages)
    if args.page_size is not None:
        collection_config = replace(collection_config, openalex_page_size=args.page_size,
                                    semantic_scholar_page_size=args.page_size)
    collection_config = replace(collection_config,
                                ambiguity_policy=getattr(args, 'ambiguity_policy', None) or 'collect_contenders')
    collector = PaperCollector([
        OpenAlexPaperProvider(providers[0], collection_config),
        SemanticScholarPaperProvider(providers[1], collection_config), DBLPPaperProvider(providers[2]),
    ])
    downloader = None
    if (args.mode or 'full') == 'full':
        from .pdf_downloader import PDFDownloader
        config = DEFAULT_DOWNLOAD
        if args.max_file_mb is not None:
            config = replace(config, maximum_file_bytes=int(args.max_file_mb * 1024 * 1024))
        downloader = PDFDownloader(client=client, config=config)
    return PaperSearchWorkflow(
        resolver=build_resolver(client, providers, llm_adjudication=getattr(args, 'llm_adjudication', False)),
        collector=collector,
        pdf_resolver=PDFResolver(client=client, unpaywall_email=args.unpaywall_email,
                                 config=replace(DEFAULT_PDF, bibliography_urls=tuple(args.bibliography_url))),
        downloader=downloader, progress=ProgressPrinter(args.verbose), collection_config=collection_config)


def _show_candidates(report, verbose=False):
    identity = report.get('identity') or {}
    candidates = identity.get('candidates') or []
    visible = candidates if verbose else candidates[:2]
    for index, candidate in enumerate(visible, 1):
        print('{}: {} | affiliations={} | score={} | confidence={} | status={}'.format(
            index, candidate['name'], ', '.join(candidate.get('matched_affiliations') or candidate.get('affiliations') or []),
            candidate.get('match_score'), candidate.get('identity_confidence'), candidate.get('status')))
        evidence = candidate.get('evidence') or []
        print('  ORCID: {}; evidence: {}'.format(candidate.get('orcid'), '; '.join(evidence if verbose else evidence[:3])))
        ids = candidate.get('author_id_aliases') or {}
        for source, author_id in candidate.get('author_ids', {}).items():
            for value in sorted(set(ids.get(source, []) + [author_id])):
                print('  --select-author {}:{}'.format(source, value))
    if len(visible) < len(candidates):
        print('{} additional candidates saved in professor.json; use --verbose for details'.format(len(candidates) - len(visible)))
    print('Continue with --input {} --select-author SOURCE:AUTHOR_ID'.format(
        Path(report['output_directory']) / 'professor.json'))


def run_arguments(parser, args):
    _validate(parser, args)
    try:
        saved = load_checkpoint(args.input) if args.input else None
        query = None if saved is not None else query_from_args(args)
        workflow = build_workflow(args)
        report = workflow.run(
            query=query, saved=saved, output_dir=args.output_dir, mode=args.mode or 'full',
            selected_author=args.select_author, include_needs_review=args.include_needs_review,
            maximum_pdf_papers=args.max_pdf_papers, maximum_downloads=args.max_downloads,
            resume_dir=args.resume_dir)
    except KeyboardInterrupt:
        print('Interrupted. Use the saved report and --resume-dir to continue.')
        return 130
    except (OSError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    if report['run_status'] == 'identity_confirmation_required':
        _show_candidates(report, args.verbose)
    collection = report.get('collection_statistics') or {}
    print('Papers: raw={}, deduplicated={}, ownership={}'.format(
        collection.get('raw_records_received', 'unknown'), collection.get('deduplicated_entries', 'unknown'),
        collection.get('ownership_counts', {})))
    if 'pdf_statistics' in report:
        statistics = report['pdf_statistics']
        print('PDF URLs: validated={}, unverified={}, unprocessed={}'.format(
            statistics.get('valid_pdf_papers', 0), statistics.get('unverified_only_papers', 0),
            statistics.get('not_processed_papers', 0)))
    if 'download_statistics' in report:
        statistics = report['download_statistics']
        print('Downloads: new={}, reused={}, content verified={}, needs review={}, failed={}'.format(
            statistics['downloaded_files'], statistics['reused_existing_papers'], statistics['verified_papers'],
            statistics['needs_review_papers'], statistics['failed_papers']))
    missing = report.get('metadata_audit', {}).get('missing_input_fields', {})
    if missing.get('year') or missing.get('authors'):
        print('Missing input metadata: year={}, authors={} (no values invented)'.format(
            missing.get('year', 0), missing.get('authors', 0)))
    if args.verbose:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    print('Status: {}; stage errors: {}; report: {}'.format(
        report['run_status'], len(report['errors']), Path(report['output_directory']) / 'report.json'))
    return 0 if report['run_status'] == 'completed' else 2


def main(argv=None):
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
    return run_arguments(parser, parser.parse_args(argv))


if __name__ == '__main__':
    raise SystemExit(main())
